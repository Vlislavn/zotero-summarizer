"""Pure helpers for the golden routes (no HTTP). Re-imported by golden.py."""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services.golden import label_provenance
from zotero_summarizer.services.library import review_detail as review_detail_svc
from zotero_summarizer.services.zotero.zotero import get_zotero_reader_or_raise


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
    reader = get_zotero_reader_or_raise()
    page = reader.get_items(
        collection_key=collection or None,
        tag=tag or None,
        search=search or None,
        limit=500,
    )
    return {str(it["item_key"]) for it in (page.get("items") or []) if it.get("item_key")}


def _build_source_payload(item_key: str) -> dict[str, Any] | None:
    """Dispatch by ``item_key`` prefix and return the source-specific
    payload, or ``None`` when the underlying data row is gone.

    Each branch is a thin wrapper that resolves dependencies (DB path,
    Zotero reader) before delegating to ``services.review_detail``.
    """
    settings_ = get_settings()
    source = review_detail_svc.classify_item_key(item_key)

    if source == review_detail_svc.SOURCE_FEED:
        feed_item_id = review_detail_svc.parse_feed_key(item_key)
        return review_detail_svc.build_feed_detail(
            triage_db_path=settings_.triage_db_path,
            zotero_data_dir=settings_.zotero_data_dir,
            feed_item_id=feed_item_id,
        )

    if source == review_detail_svc.SOURCE_NOTE:
        parent_key, note_id = review_detail_svc.parse_note_key(item_key)
        reader = get_zotero_reader_or_raise()
        return review_detail_svc.build_note_detail(reader, parent_key, note_id)

    reader = get_zotero_reader_or_raise()
    return review_detail_svc.build_library_detail(reader, item_key)


def _append_verdict_golden(item_key: str, priority: str, comment: str) -> None:
    """Write a golden training row for a user verdict on any item_key.

    Without this, a verdict on a materialized-but-unread library item never
    reaches the classifier: the engagement-only export produced no row for it,
    and the hybrid overlay only overrides existing rows. ``append_verdict_to_golden``
    is idempotent — when a row already exists the overlay covers it. No-op when
    the live source is gone (no metadata to build a trainable row)."""
    from zotero_summarizer.services.library import review

    payload = _build_source_payload(item_key)
    if payload is None:
        return
    authors = "; ".join(
        str(a.get("name") or "") for a in (payload.get("authors") or []) if a.get("name")
    )
    review.append_verdict_to_golden(
        item_key,
        title=str(payload.get("title") or ""),
        abstract=str(payload.get("abstract") or ""),
        priority=priority,
        authors=authors,
        venue=str(payload.get("venue") or ""),
        year=str(payload.get("year") or ""),
        doi=str(payload.get("doi") or ""),
        comment=comment,
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
    from zotero_summarizer.services.model.classifier_persistence import DEFAULT_MODEL_DIR

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
            DEFAULT_MODEL_DIR, golden_sha,
            [_suggestion_to_dict(s) for s in suggestions],
        )
    except Exception as exc:  # noqa: BLE001 — background worker boundary
        border_cache.finish(error=f"{type(exc).__name__}: {exc}")
        return
    border_cache.finish(error=None)

