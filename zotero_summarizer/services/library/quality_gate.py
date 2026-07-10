"""The auto QUALITY GATE — quality as a CONJUNCTIVE hard filter, not an additive bonus.

WHY THIS EXISTS. The user's ranking philosophy: ``quality ∧ match``, with quality as a
HARD filter. A good slightly-off-topic paper is fine; a bad paper on-topic must NOT
surface. Today the opposite holds — the fleet verdict is advisory (``reading_queue``:
"display-only sidecar, never fed to the hide/pin logic"), the blend treats quality as an
additive ``+0.06`` bonus, and 0 manual hides leave 57% D/flag garbage in Read-next.

This module is the precision SURFACE fix: a NEW, separate, ENFORCED auto-gate that
writes ``dont_read`` verdicts with ``source = VERDICT_SOURCE_AUTO_QUALITY``. It does NOT
touch the advisory fleet (that stays a SUGGESTION). Two cascaded layers:

```
 L1 — TRIAGE (99% coverage, abstract-based, cheap): LLM relevance_score <= floor → hide
 L2 — DEEP-REVIEW (29% coverage, full-text, rigorous): grade D | band flag → hide
```

DISCIPLINE:
* **No hide on absent evidence.** ``None`` signals → no hide (the documented empty
  contract — absence of evidence is never evidence to hide, same rule as
  ``propose_verdict``). This is a contract, not error-masking: a missing LLM score or
  grade means "not assessed", and the gate declines to act rather than guess.
* **Never clobber a human.** A row with an existing ``source=user`` verdict is skipped
  (the UPSERT in ``insert_or_update_label_verdict`` would overwrite, but we don't even
  call it). One-tap restore already works: a manual relabel flips the row back to ``user``.
* **Fail-fast at the I/O boundary.** DB errors propagate from ``apply_auto_quality_gate``
  (per-item boundary) — never swallowed. The pure ``should_auto_hide`` has no I/O.

See the plan: ``.claude/plans/quality-as-gate-not-bonus.md``.
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
# source values that represent a HUMAN decision the gate must never overwrite.
_PROTECTED_SOURCES = (VERDICT_SOURCE_USER,)


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
    only_keys: set[str] | None = None,
) -> int:
    """Scan shown rows and auto-hide the bad-quality ones. Returns the count hidden.

    ``db_path`` is the canonical ``Settings.triage_db_path`` (passed by the caller —
    matches the ``storage._repo_labels`` convention; never hardcoded here). Reads
    ``processed_feed_items`` (shown priorities) + the existing ``label_verdicts`` (to
    skip human-verdicted rows) read-only; writes a ``dont_read`` verdict per hide via
    ``insert_or_update_label_verdict`` with ``source=VERDICT_SOURCE_AUTO_QUALITY``.

    ``only_keys``: when set (the deep-review settle hook passes the just-reviewed keys),
    restrict L2 to those; L1 still applies to all shown rows in the pass. ``reviews`` is
    the ``deep_review._read_all()`` cache (keyed by ``materialized_zotero_key``).

    Fail-fast: a DB error propagates from this boundary (the caller logs + skips the
    item, never swallows). A row whose LLM score is unparseable is SKIPPED (no hide on
    a corrupt payload) — the ``json.JSONDecodeError`` is caught narrowly at this I/O
    boundary and the row is left for the human, not guessed about.
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
        if prior is not None and prior.get("source") in _PROTECTED_SOURCES:
            continue
        # L2 only fires for the requested keys when the caller scoped the pass
        # (the deep-review hook); L1 (LLM score) applies regardless — it's already
        # computed for every triaged row, no extra cost.
        if only_keys is not None and item_key not in only_keys:
            quality: dict[str, Any] | None = None
        else:
            quality = (reviews.get(item_key) or {}).get("quality") or {}
        try:
            llm_score = _llm_relevance_score(r["shap_contribs_json"])
        except json.JSONDecodeError:
            # Corrupt payload — skip this row (no hide on unparseable data). Narrow
            # catch at the I/O boundary; the row is left for the human, not guessed.
            continue
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
    enabled_env = os.environ.get("ZS_AUTO_QUALITY_GATE")
    if enabled_env is not None:
        enabled = _as_bool(enabled_env)
    else:
        app_state = getattr(state(), "app_state", None)
        config = getattr(app_state, "config", None) if app_state is not None else None
        qr = getattr(config, "quality_review", None) if config is not None else None
        enabled = bool(getattr(qr, "auto_quality_gate", True))
    floor_env = os.environ.get("ZS_AUTO_QUALITY_LLM_FLOOR")
    app_state = getattr(state(), "app_state", None)
    config = getattr(app_state, "config", None) if app_state is not None else None
    qr = getattr(config, "quality_review", None) if config is not None else None
    floor = int(floor_env) if floor_env is not None else int(getattr(qr, "auto_quality_llm_floor", 2))
    grades_env = os.environ.get("ZS_AUTO_QUALITY_HIDE_GRADES")
    grades = tuple(grades_env.split(",")) if grades_env is not None else tuple(getattr(qr, "auto_quality_hide_grades", ("D",)))
    bands_env = os.environ.get("ZS_AUTO_QUALITY_HIDE_BANDS")
    bands = tuple(bands_env.split(",")) if bands_env is not None else tuple(getattr(qr, "auto_quality_hide_bands", ("flag",)))
    return enabled, floor, grades, bands


def _run_gate(only_keys: set[str] | None, *, where: str) -> int:
    """Shared non-blocking runner. ``where`` labels the log on failure.

    BOUNDARY CONTRACT — non-blocking: an EXPECTED I/O failure (DB locked, bad config
    value) is logged at WARNING and does NOT propagate. The caller's primary work
    (a deep review, a triage tick) already SUCCEEDED; the gate is a separate, additive
    precision step, and a failure here must not abort it. Hidden rows self-heal on the
    next pass. Narrow to expected failure types — a real BUG (TypeError/AttributeError/
    KeyError) still propagates so it surfaces, not silently logged. User-authorized
    (the gate exists by the user's explicit precision-mode ask)."""
    import logging
    enabled, floor, grades, bands = _gate_config()
    if not enabled:
        return 0
    if only_keys is not None and not only_keys:
        return 0
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.services.library import deep_review
    try:
        return apply_auto_quality_gate(
            get_settings().triage_db_path, deep_review._read_all(),
            llm_floor=floor, hide_grades=grades, hide_bands=bands, only_keys=only_keys,
        )
    except (sqlite3.Error, OSError, ValueError) as exc:  # expected I/O only — bugs propagate
        logging.getLogger(__name__).warning(
            "auto quality-gate (%s) failed: %s", where, exc)
        return 0


def fire_for_keys(only_keys: set[str]) -> int:
    """Post-review hook (L2): fire the gate scoped to the just-reviewed keys.

    Called from ``deep_review._review_worker`` after ``_write_one`` persists the
    review — the grade is now in the cache. L2 (grade D | flag) hides for these keys;
    L1 (LLM-score floor) also runs over the whole shown surface in the same pass. A
    gate failure is non-blocking (see ``_run_gate``): the review already SUCCEEDED and
    must not be flipped to ``error``."""
    return _run_gate(only_keys, where="fire_for_keys")


def fire_full() -> int:
    """Whole-surface pass (L1 + L2): fire the gate over ALL shown rows.

    Called from the triage tick after daily selection (newly-triaged rows just landed
    with LLM scores in ``shap_contribs_json``) — covers the shown rows that never get a
    deep review (the L1 floor reaches them) plus a fresh L2 over the review cache.
    Non-blocking (see ``_run_gate``): a failure never aborts the tick."""
    return _run_gate(None, where="fire_full")


