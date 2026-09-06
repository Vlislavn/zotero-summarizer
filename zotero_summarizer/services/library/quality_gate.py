"""Explicit triage automation: hide shown papers by relevance floor or quality.

``fire_full`` runs only from the triage tick, never as a review-completion hook.
Absent evidence does not hide; existing human labels are preserved. Model-derived
quality remains separate from the fleet's advisory proposals. I/O errors propagate.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from zotero_summarizer.domain import VERDICT_SOURCE_AUTO_QUALITY, VERDICT_SOURCE_USER
from zotero_summarizer.storage.repositories import (
    insert_or_update_label_verdict,
    list_all_label_verdicts,
)

# The 3 "shown" priorities — the gate only acts on what would surface in Read-next.
# ``dont_read`` is already handled (hidden); ``selected``/``black_swan`` are allocator
# outputs, not a reading-queue surface, so they're excluded too.
_SHOWN_PRIORITIES = ("could_read", "should_read", "must_read")


def should_auto_hide(
    *,
    relevance_score: int | None,
    quality: dict[str, Any] | None,
    llm_floor: int = 2,
    hide_grades: tuple[str, ...] = ("D",),
    hide_bands: tuple[str, ...] = ("flag",),
) -> tuple[bool, str]:
    """``(hide, reason)`` for one row. Pure — no I/O.

    L1: ``relevance_score`` (the feed-stage LLM's 1-5) ``<= llm_floor`` → hide.
    L2: deep-review ``grade`` in ``hide_grades`` OR ``quality_band`` in ``hide_bands``.
    A ``None`` signal is NOT a hide (absent evidence ≠ bad quality). ``red_flags`` /
    ``overstatements`` alone do NOT hide — they already cut the fleet confidence + render
    a ⚠️ chip; hiding on them alone is too aggressive. D/flag is the full-text "bad
    quality" verdict.
    """
    # L2 first (full-text evidence outranks the abstract-based LLM score when both exist).
    q = quality or {}
    grade = str(q.get("grade") or "").strip().upper()
    band = str(q.get("quality_band") or "").strip().lower()
    if grade and grade in hide_grades:
        return True, f"L2 grade {grade}"
    if band and band in hide_bands:
        return True, f"L2 band {band}"
    # L1: the abstract-based LLM score floor.
    if relevance_score is not None and int(relevance_score) <= llm_floor:
        return True, f"L1 llm_score {relevance_score}<={llm_floor}"
    return False, ""


def _llm_relevance_score(shap_json: str | None) -> int | None:
    """The feed-stage LLM's 1-5 from ``shap_contribs_json.summary.relevance_score``.
    Returns ``None`` when absent (the documented "not assessed" contract). Raises on
    malformed JSON — the I/O boundary fails fast rather than masking a corrupt row."""
    raw = (shap_json or "").strip()
    if not raw:
        return None
    score = json.loads(raw).get("summary", {}).get("relevance_score")
    return int(score) if score is not None else None


def apply_auto_quality_gate(
    db_path,
    reviews: dict[str, Any],
    *,
    llm_floor: int = 2,
    hide_grades: tuple[str, ...] = ("D",),
    hide_bands: tuple[str, ...] = ("flag",),
) -> int:
    """Scan shown rows and auto-hide the bad-quality ones. Returns the count hidden.

    ``db_path`` is the canonical ``Settings.triage_db_path`` (passed by the caller —
    matches the ``storage._repo_labels`` convention; never hardcoded here). Reads
    ``processed_feed_items`` (shown priorities) + the existing ``label_verdicts`` (to
    skip human-verdicted rows) read-only; writes a ``dont_read`` verdict per hide via
    ``insert_or_update_label_verdict`` with ``source=VERDICT_SOURCE_AUTO_QUALITY``.

    ``reviews`` is the deep-review cache keyed by ``materialized_zotero_key``.
    DB and malformed-payload errors propagate. Each hide commits separately;
    this is not a transaction over the full pass or concurrent human decisions.
    """
    # Existing verdicts by item_key — skip any a human authored (never clobber).
    existing = {v["item_key"]: v for v in list_all_label_verdicts(db_path)}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(_SHOWN_PRIORITIES))
        rows = conn.execute(
            f"""SELECT materialized_zotero_key, reading_priority, shap_contribs_json
                FROM processed_feed_items
                WHERE materialized_zotero_key IS NOT NULL
                  AND reading_priority IN ({placeholders})""",
            _SHOWN_PRIORITIES,
        ).fetchall()
    finally:
        conn.close()

    hidden = 0
    for r in rows:
        item_key = (r["materialized_zotero_key"] or "").strip()
        if not item_key:
            continue
        # Never clobber a human verdict.
        prior = existing.get(item_key)
        if prior is not None and prior.get("source") == VERDICT_SOURCE_USER:
            continue
        quality = (reviews.get(item_key) or {}).get("quality") or {}
        llm_score = _llm_relevance_score(r["shap_contribs_json"])
        hide, reason = should_auto_hide(
            relevance_score=llm_score, quality=quality,
            llm_floor=llm_floor, hide_grades=hide_grades, hide_bands=hide_bands,
        )
        if not hide:
            continue
        insert_or_update_label_verdict(
            db_path,
            item_key=item_key,
            original_derived_priority=r["reading_priority"] or "could_read",
            user_priority="dont_read",
            comment=f"auto quality gate: {reason}",
            source=VERDICT_SOURCE_AUTO_QUALITY,
        )
        hidden += 1
    return hidden


def _gate_config() -> tuple[bool, int, tuple[str, ...], tuple[str, ...]]:
    """Read the gate knobs from the live goals config (env wins). Returns
    ``(enabled, llm_floor, hide_grades, hide_bands)``. Optional-feature boundary:
    no config / no state → the ON / floor 2 / {D} / {flag} defaults (cold-start safe,
    mirrors ``_common.band_primary_enabled``)."""
    from zotero_summarizer.services._common import state
    from zotero_summarizer.services.config_overrides import _as_bool
    app_state = getattr(state(), "app_state", None)
    config = getattr(app_state, "config", None) if app_state is not None else None
    qr = getattr(config, "quality_review", None) if config is not None else None
    enabled_env = os.environ.get("ZS_AUTO_QUALITY_GATE")
    if enabled_env is not None:
        enabled = _as_bool(enabled_env)
    else:
        enabled = bool(getattr(qr, "auto_quality_gate", True))
    floor_env = os.environ.get("ZS_AUTO_QUALITY_LLM_FLOOR")
    floor = int(floor_env) if floor_env is not None else int(getattr(qr, "auto_quality_llm_floor", 2))
    grades_env = os.environ.get("ZS_AUTO_QUALITY_HIDE_GRADES")
    grades = tuple(grades_env.split(",")) if grades_env is not None else tuple(getattr(qr, "auto_quality_hide_grades", ("D",)))
    bands_env = os.environ.get("ZS_AUTO_QUALITY_HIDE_BANDS")
    bands = tuple(bands_env.split(",")) if bands_env is not None else tuple(getattr(qr, "auto_quality_hide_bands", ("flag",)))
    return enabled, floor, grades, bands


def fire_full() -> int:
    """Apply configured L1/L2 filtering to all shown rows during a triage tick.

    Disabled automation returns zero; cache/config/DB failures propagate to the
    tick's error boundary, rather than masquerading as a successful empty pass.
    """
    enabled, floor, grades, bands = _gate_config()
    if not enabled:
        return 0
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.services.library import deep_review
    return apply_auto_quality_gate(
        get_settings().triage_db_path, deep_review._read_all(),
        llm_floor=floor, hide_grades=grades, hide_bands=bands,
    )

