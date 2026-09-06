"""Pure helpers for the golden routes (no HTTP). Re-imported by golden.py."""

from __future__ import annotations

from typing import Any

from zotero_summarizer.services import interaction_log
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.golden import label_provenance
from zotero_summarizer.services.golden.verdict_effects import (
    add_feed_verdict_to_library,
    source_payload as _build_source_payload,
)
from zotero_summarizer.services.zotero.zotero import get_library_reader


def _golden_csv_path():
    return get_settings().golden_csv_path


def _db_path():
    return get_settings().triage_db_path


def _load_all():
    """Load every row's provenance. Fail-fast if the CSV is missing."""
    return label_provenance.load_golden_provenance(_golden_csv_path())


def _zotero_candidate_keys(*, collection: str, tag: str, search: str) -> set[str]:
    """Item keys matching the Zotero collection/tag/search filters — the SAME
    reader filtering the Library queue uses, so annotate filters consistently.
    One query, capped at the reader's 500-item scan window."""
    reader = get_library_reader()
    if hasattr(reader, "get_items"):
        page = reader.get_items(
            collection_key=collection or None,
            tag=tag or None,
            search=search or None,
            limit=500,
        )
    else:
        page = reader.get_all_items(
            collection_key=collection or None,
            tag=tag or None,
            search=search or None,
            include_abstract=False,
        )
    return {
        str(it["item_key"]) for it in (page.get("items") or []) if it.get("item_key")
    }


def log_verdict_event(
    item_key: str, original: str, user_priority: str, comment: str
) -> None:
    """Append the annotate-verdict human-feedback event (model's derived priority
    + the human's label) to the agentic interaction log."""
    interaction_log.log_human_feedback(
        item_key=item_key,
        item_key_kind=interaction_log.key_kind(item_key),
        surface="annotate_verdict",
        model={"priority": original},
        human={"kind": "priority", "value": user_priority},
        comment=comment,
    )


def log_retract_event(item_key: str, prior: dict[str, Any]) -> None:
    """Append the verdict-DELETE retraction event — the trajectory the
    UPSERT/DELETE ``label_verdicts`` table erases. ``prior`` is the row read
    BEFORE the delete (its model/human pair is gone afterwards)."""
    interaction_log.log_human_feedback(
        item_key=item_key,
        item_key_kind=interaction_log.key_kind(item_key),
        surface="annotate_retract",
        model={"priority": prior["original_derived_priority"]},
        human={"kind": "retract", "value": prior["user_priority"]},
    )


def _suggestion_to_dict(s: Any) -> dict[str, Any]:
    return {
        "item_key": s.item_key,
        "title": s.title,
        "authors": s.authors,
        "venue": s.venue,
        "abstract_preview": s.abstract_preview,
        "predicted_score": round(s.predicted_score, 4),
        "predicted_priority": s.predicted_priority,
        "current_priority": s.current_priority,
        "border_distance": round(s.border_distance, 4),
        "disagrees": s.disagrees,
        "has_label": s.has_label,
    }


def _compute_border_into_cache(golden_sha: str, top_k: int) -> None:
    """Background worker: score library rows, persist to the sha-keyed cache.

    Runs off the request thread because scoring ~740 library rows costs
    ~1 s each (OpenAlex enrichment). Captures its own exceptions into the
    border_cache job state — a background worker has no caller to receive
    them, so swallow-and-record is the documented exception to fail-fast.
    """
    from zotero_summarizer.services.model import active_learning
    from zotero_summarizer.services.library import border_cache
    from zotero_summarizer.services._common import read_config

    try:
        settings = get_settings()
        csv_path = _golden_csv_path()
        rows = active_learning.load_rows(csv_path)
        goals_config = read_config(settings.config_path)
        suggestions = active_learning.suggest_border_labels(
            rows,
            corpus_db_path=settings.corpus_db_path,
            goals_config=goals_config,
            golden_csv=csv_path,
            classifier_name="lightgbm",
            top_k=max(int(top_k), 200),  # cache a generous slice; UI slices further
            db_path=settings.triage_db_path,  # anchor disagreement to label:* truth
        )
        border_cache.write_cache(
            settings.model_dir,
            golden_sha,
            [_suggestion_to_dict(s) for s in suggestions],
        )
    except Exception as exc:  # noqa: BLE001 — background worker boundary
        border_cache.finish(error=f"{type(exc).__name__}: {exc}")
        return
    border_cache.finish(error=None)
