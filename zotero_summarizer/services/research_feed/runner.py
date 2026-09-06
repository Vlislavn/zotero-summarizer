"""Bounded weekly Research Intelligence orchestration over existing app artifacts."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage
from zotero_summarizer.services._common import now_iso_z, write_json_atomic
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.research_feed.card import build_card
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.services.research_feed.render import persist
from zotero_summarizer.services.research_feed.source import RssCandidateSource, deduplicate
from zotero_summarizer.settings import Settings


ReviewLoader = Callable[[str], dict[str, Any] | None]


def _words(value: str) -> set[str]:
    return {word for word in "".join(ch.lower() if ch.isalnum() else " " for ch in value).split()
            if len(word) >= 5}


def _matches(labels: list[str], text: str) -> list[str]:
    words = _words(text)
    return [label for label in labels if words & _words(label)]


def _latest_rows(db_path: Path) -> dict[str, dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM processed_feed_items WHERE stable_feed_key IS NOT NULL ORDER BY id DESC"
        ).fetchall()
    finally:
        conn.close()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest.setdefault(str(row["stable_feed_key"]), dict(row))
    return latest


def _summary(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    try:
        return (json.loads(row.get("shap_contribs_json") or "{}") or {}).get("summary") or {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _contributions(text: str) -> list[str]:
    lower = text.lower()
    kinds = [kind for kind in ("method", "benchmark", "dataset", "evaluation", "analysis")
             if kind in lower]
    return kinds or ["analysis"]


def triage_candidate(candidate: ResearchCandidate, row: dict[str, Any] | None, profile) -> ResearchFeedTriage:
    summary = _summary(row)
    text = " ".join([candidate.title, candidate.abstract, str(summary.get("executive_summary") or ""),
                     str(summary.get("methods") or "")])
    themes, projects = _matches(profile.themes, text), _matches(profile.projects, text)
    priority_score = {"must_read": 5, "should_read": 4, "could_read": 3, "dont_read": 1}
    raw_score = float((row or {}).get("composite_score") or summary.get("composite_relevance_score") or 1)
    score = priority_score.get(str((row or {}).get("reading_priority") or ""), round(raw_score))
    score = max(1, min(5, score))
    method = summary.get("method_and_code")
    if isinstance(method, dict) and method.get("artifacts"):
        artifact = "present"
    elif "method_and_code" in summary:
        artifact = "absent"
    else:
        artifact = "unknown"
    decision = str((row or {}).get("decision") or "")
    include = decision == "selected" or (
        decision not in {"user_rejected", "gate_rejected"} and score >= 3 and bool(themes or projects)
    )
    reason = str(summary.get("triage_rationale") or (row or {}).get("decision_reason") or "No prior triage evidence.")
    return ResearchFeedTriage(
        include=include, score=score,
        confidence=float(summary.get("triage_confidence") or 0.5),
        matched_themes=themes, matched_projects=projects,
        contribution_kind=_contributions(text), artifact_signal=artifact,
        novelty_claim=str(method.get("what_is_new") or "") if isinstance(method, dict) else "",
        rationale=reason,
    )


def _queue_tags(
    settings: Settings, records: list[dict[str, Any]], rows: dict[str, dict[str, Any]],
) -> dict[str, int]:
    from zotero_summarizer.storage import repositories

    counts = {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    with repositories.with_db_path(settings.triage_db_path):
        existing = repositories.get_pending_changes(status=None, limit=5000)
        signatures = {(row["item_key"], row["payload_json"]) for row in existing
                      if row["change_type"] == "tag_changes"}
        for record in records:
            source_id, card = record["candidate"]["source_id"], record["card"]
            item_key = str((rows.get(source_id) or {}).get("materialized_zotero_key") or "")
            if not item_key:
                counts["skipped"] += 1
                continue
            tags = ["ri:weekly", f"ri:{card['worth_reading']}",
                    *[f"ri:{tag}" for tag in card["topic_tags"]],
                    *[f"ri:project:{value}" for value in record["triage"]["matched_projects"]]]
            payload = {"add_tags": sorted(set(tags)), "remove_tags": []}
            payload_json = json.dumps(payload, ensure_ascii=False)
            if (item_key, payload_json) in signatures:
                counts["skipped"] += 1
                continue
            counts["attempted"] += 1
            try:
                inserted = repositories.insert_pending_changes(
                    item_key, record["candidate"]["title"],
                    [{"change_type": "tag_changes", "payload": payload}],
                )
            except Exception:  # noqa: BLE001 - isolate optional per-paper writebacks.
                counts["failed"] += 1
                continue
            counts["succeeded"] += inserted
            signatures.add((item_key, payload_json))
    return counts


def _assess(
    settings: Settings, start: datetime, end: datetime, source_limit: int, venue: str,
) -> tuple[Any, list[ResearchCandidate], dict[str, dict[str, Any]], list[tuple[Any, Any]], int]:
    profile = load_profile(settings.data_dir)
    discovered = RssCandidateSource(settings.triage_db_path).load(
        start=start, end=end, limit=source_limit,
    )
    candidates = deduplicate(discovered)
    if venue:
        candidates = [candidate for candidate in candidates
                      if venue.casefold() in str(candidate.venue or "").casefold()]
    rows = _latest_rows(settings.triage_db_path)
    assessed = [(candidate, triage_candidate(candidate, rows.get(candidate.source_id), profile))
                for candidate in candidates]
    return profile, candidates, rows, assessed, len(discovered)


def _ensure_reviews(settings: Settings, candidates: list[ResearchCandidate], timeout_seconds: int) -> None:
    """Run the existing full-text/deep-review path for cache misses, then wait."""
    from zotero_summarizer.services.library import deep_review
    from zotero_summarizer.services.library.app_library_reader import AppLibraryReader

    keys = [candidate.source_id for candidate in candidates
            if deep_review.get_current_review(candidate.source_id) is None]
    if not keys:
        return
    deep_review.start(
        item_keys=keys, reader=AppLibraryReader(settings.triage_db_path), acquire_missing=True,
    )
    deadline = time.monotonic() + max(30, timeout_seconds)
    while any(deep_review.status(key)["status"] == "running" for key in keys):
        if time.monotonic() >= deadline:
            return
        time.sleep(0.2)


def _records(
    shortlist: list[tuple[ResearchCandidate, ResearchFeedTriage]],
    review_loader: ReviewLoader,
    profile: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for candidate, triage in shortlist:
        try:
            review = review_loader(candidate.source_id)
            if review is None or not review.get("digest"):
                missing.append(candidate.model_dump(mode="json"))
                continue
            card = build_card(candidate, triage, review, profile)
            records.append({
                "candidate": candidate.model_dump(mode="json"),
                "triage": triage.model_dump(mode="json"),
                "card": card.model_dump(mode="json"),
                "provenance": {
                    "source_id": candidate.source_id,
                    "pipeline_stage": "deep_review_projection",
                    "review_contract_version": review.get("review_contract_version"),
                    "provider": (review.get("provenance") or {}).get("provider"),
                    "model": (review.get("provenance") or {}).get("model"),
                    "prompt_schema_version": (review.get("provenance") or {}).get("prompt_schema_version"),
                    "generated_at": review.get("reviewed_at"),
                    "basis": (review.get("digest") or {}).get("basis", "unknown"),
                    "confidence": triage.confidence,
                    "abstained": False,
                },
            })
        except Exception as exc:  # noqa: BLE001 - one paper never fails the weekly run.
            failed.append({"source_id": candidate.source_id, "error": f"{type(exc).__name__}: {exc}"})
    return records, missing, failed


def _payload(
    metadata: dict[str, Any], records: list[dict[str, Any]], missing: list[dict[str, Any]],
    failed: list[dict[str, str]], rejects: list[tuple[Any, Any]],
) -> dict[str, Any]:
    return {
        "metadata": metadata,
        "cards": records,
        "rejected_near_threshold": [
            {"candidate": candidate.model_dump(mode="json"), "triage": triage.model_dump(mode="json")}
            for candidate, triage in rejects
        ],
        "research_ideas_by_project": _ideas_by_project(records),
        "manual_full_text_required": missing,
        "failed": failed,
    }


def run_weekly(
    settings: Settings,
    *,
    start: datetime,
    end: datetime,
    source_limit: int = 1000,
    shortlist_budget: int | None = None,
    card_budget: int | None = None,
    venue: str = "",
    dry_run: bool = True,
    queue_zotero: bool = False,
    generate_reviews: bool = False,
    review_timeout_seconds: int = 3600,
    review_loader: ReviewLoader = _review_cache.get_current_review,
) -> dict[str, Any]:
    profile, candidates, rows, assessed, discovered = _assess(
        settings, start, end, source_limit, venue,
    )
    included = sorted((pair for pair in assessed if pair[1].include),
                      key=lambda pair: (pair[1].score, pair[1].confidence, pair[0].title), reverse=True)
    shortlist = included[:shortlist_budget or profile.shortlist_budget]
    reviewed = shortlist[:card_budget or profile.card_budget]
    if generate_reviews:
        _ensure_reviews(settings, [candidate for candidate, _triage in reviewed], review_timeout_seconds)
    records, missing, failed = _records(reviewed, review_loader, profile)
    rejects = sorted((pair for pair in assessed if not pair[1].include),
                     key=lambda pair: pair[1].score, reverse=True)[:10]
    writebacks = (
        _queue_tags(settings, records, rows)
        if queue_zotero and not dry_run else {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    )
    metadata = {
        "schema_version": 1, "prompt_version": "existing-triage+deep-review-v2",
        "generated_at": now_iso_z(), "from": start.isoformat(), "to": end.isoformat(),
        "source": "app_rss", "venue": venue or None, "dry_run": dry_run,
        "counts": {
            "discovered": discovered, "deduplicated": len(candidates),
            "triaged": len(assessed), "shortlisted": len(shortlist),
            "full_text_available": len(records), "cards_generated": len(records),
            "failed": len(failed),
        },
        "writebacks": writebacks,
    }
    payload = _payload(metadata, records, missing, failed, rejects)
    output_dir = settings.data_dir / "research_feed"
    slug = f"weekly-{end.date().isoformat()}"
    json_path, md_path = persist(payload, output_dir, slug)
    write_json_atomic(output_dir / "state.json", {
        "schema_version": 1, "last_from": start.isoformat(), "last_to": end.isoformat(),
        "last_run_at": now_iso_z(), "last_json": str(json_path), "writebacks": writebacks,
    })
    return {"json_path": str(json_path), "markdown_path": str(md_path),
            "writebacks_queued": writebacks["succeeded"], **payload["metadata"]["counts"]}


def _ideas_by_project(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        for idea in record["card"]["research_ideas"]:
            grouped.setdefault(idea["target_project"], []).append(idea)
    return grouped


__all__ = ["run_weekly", "triage_candidate"]
