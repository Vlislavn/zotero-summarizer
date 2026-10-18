"""Bounded weekly Research Intelligence orchestration over existing app artifacts."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from datetime import datetime
import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage
from zotero_summarizer.models.research_feed import parse_run_budgets
from zotero_summarizer.services._common import now_iso_z, write_json_atomic
from zotero_summarizer.services.library import _feed_assessment
from zotero_summarizer.services.research_feed.cursor import CandidateCursor
from zotero_summarizer.services.research_feed.card import build_card
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.services.research_feed.render import persist
from zotero_summarizer.services.research_feed.source import CandidateReader, admit_history, load_candidates, number, summary
from zotero_summarizer.services.research_feed.ranking import rank_candidates
from zotero_summarizer.settings import Settings


ReviewLoader = Callable[[str], dict[str, Any] | None]
LOGGER = logging.getLogger(__name__)


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


def _contributions(text: str) -> list[str]:
    lower = text.lower()
    kinds = [kind for kind in ("method", "benchmark", "dataset", "evaluation", "analysis")
             if kind in lower]
    return kinds or ["analysis"]


def triage_candidate(candidate: ResearchCandidate, row: dict[str, Any] | None, profile) -> ResearchFeedTriage:
    stored = summary(row)
    text = " ".join([candidate.title, candidate.abstract, str(stored.get("executive_summary") or ""),
                     str(stored.get("methods") or "")])
    themes, projects = _matches(profile.themes, text), _matches(profile.projects, text)
    priority_score = {"must_read": 5, "should_read": 4, "could_read": 3, "dont_read": 1}
    raw_score = (row or {}).get("composite_score")
    raw_score = number(stored.get("composite_relevance_score") if raw_score is None else raw_score, default=1)
    score = priority_score.get(str((row or {}).get("reading_priority") or ""), round(raw_score))
    score = max(1, min(5, score))
    method = stored.get("method_and_code")
    if isinstance(method, dict) and method.get("artifacts"):
        artifact = "present"
    elif "method_and_code" in stored:
        artifact = "absent"
    else:
        artifact = "unknown"
    decision = str((row or {}).get("decision") or "")
    if decision not in {"", "selected", "black_swan", "user_approved", "user_rejected", "gate_rejected",
                        "rejected_low_score", "rejected_dedup_library", "rejected_dedup_processed", "triaged_pending",
                        "awaiting_review", "rejected_daily_cutoff", "rejected_elbow"}:
        raise ValueError("prior triage decision failed or invalid")
    veto = {"user_rejected", "gate_rejected", "rejected_low_score", "rejected_dedup_library", "rejected_dedup_processed"}
    include = decision == "selected" or (decision not in veto and score >= 3 and bool(themes or projects))
    reason = str(stored.get("triage_rationale") or (row or {}).get("decision_reason") or "No prior triage evidence.")
    return ResearchFeedTriage(
        include=include, score=score,
        confidence=number(stored.get("triage_confidence"), default=0.5, maximum=1),
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
        queued_signatures: set[tuple[str, str]] = set()
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
            signature = (item_key, payload_json)
            if signature in queued_signatures:
                counts["skipped"] += 1
                continue
            try:
                inserted = repositories.insert_pending_change_if_absent(
                    item_key, record["candidate"]["title"], "tag_changes", payload,
                )
            except Exception:  # noqa: BLE001 - isolate optional per-paper writebacks.
                counts["attempted"] += 1
                counts["failed"] += 1
                continue
            if inserted:
                counts["attempted"] += 1
                counts["succeeded"] += 1
                queued_signatures.add(signature)
            else:
                counts["skipped"] += 1
    return counts


def _assess(
    settings: Settings, start: datetime, end: datetime, source_limit: int, venue: str,
    *, generate: bool = False,
) -> tuple[Any, list[ResearchCandidate], dict[str, dict[str, Any]], list[tuple[Any, Any]], list[dict[str, str]]]:
    profile = load_profile(settings.data_dir)
    candidates = load_candidates(
        settings.triage_db_path, start=start, end=end, limit=source_limit, venue=venue,
    )
    rows = _latest_rows(settings.triage_db_path)
    assessed, failed = [], []
    cursor = CandidateCursor(settings)
    for candidate in candidates:
        try:
            row = rows.get(candidate.source_id)
            candidate = admit_history(candidate, row)
            if row is None:
                row = cursor.row(candidate, profile, generate=generate)
                if row is not None:
                    rows[candidate.source_id] = row
            assessed.append((candidate, triage_candidate(candidate, row, profile)))
        except Exception as exc:  # noqa: BLE001 — malformed history is isolated per paper.
            LOGGER.warning("research-feed triage failed source=%s: %s", candidate.source_id, exc)
            failed.append({"source_id": candidate.source_id, "error": f"{type(exc).__name__}: {exc}"})
    return profile, candidates, rows, assessed, failed


def _ensure_reviews(
    settings: Settings, candidates: list[ResearchCandidate], timeout_seconds: int, profile: Any = None,
) -> dict[str, str]:
    """Run reviews for cache misses and return per-key timeout/error outcomes."""
    from zotero_summarizer.services.library import deep_review
    from zotero_summarizer.services.library.app_library_reader import AppLibraryReader

    keys, outcomes, overrides = [], {}, {}
    contexts = {_c.source_id: _feed_assessment.context_for(_c.source_id, profile) for _c in candidates} if profile else {}
    for candidate in candidates:
        key = candidate.source_id
        try:
            review = _feed_assessment.current_review(key) if profile else deep_review.get_current_review(key)
            if review and review.get("review_identity", {}).get("source_path"):
                overrides[key] = review["review_identity"]["source_path"]
            _feed_assessment.review_evidence(review)
            assessment = _feed_assessment.qualify(review, contexts[key]) if profile else {}
            if profile and assessment["status"] == "not_assessed":
                keys.append(key)
        except ValueError:
            keys.append(key)
        except Exception as exc:  # noqa: BLE001 — source/currentness errors stay per-paper errors.
            outcomes[key] = f"current review failed: {type(exc).__name__}: {exc}"
    if not keys:
        return outcomes
    try:
        deep_review.start(
            item_keys=keys, acquire_missing=True,
            reader=CandidateReader(settings.triage_db_path, candidates) if profile else AppLibraryReader(settings.triage_db_path),
            **({"feed_contexts": {key: contexts[key] for key in keys}, "pdf_overrides": overrides} if profile else {}),
        )
    except Exception as exc:  # noqa: BLE001 — report start failure per affected paper.
        return {**outcomes, **{key: f"deep review start failed: {type(exc).__name__}: {exc}" for key in keys}}
    deadline = time.monotonic() + timeout_seconds
    pending = set(keys)
    while pending:
        for key in tuple(pending):
            job = deep_review.status(key)
            if job.get("status") == "running":
                continue
            pending.remove(key)
            if job.get("status") == "error":
                outcomes[key] = str(job.get("error") or "deep review failed")
            elif profile:
                try:
                    review = _feed_assessment.current_review(key)
                    _feed_assessment.review_evidence(review)
                    if _feed_assessment.qualify(review, contexts[key])["status"] == "not_assessed":
                        raise ValueError("feed assessment missing after generation")
                except Exception as exc:  # noqa: BLE001 — do not publish failed qualification as science.
                    outcomes[key] = f"review qualification failed: {type(exc).__name__}: {exc}"
        if not pending:
            break
        if time.monotonic() >= deadline:
            for key in pending:
                outcomes[key] = f"deep review timed out after {timeout_seconds}s"
            break
        time.sleep(0.2)
    return outcomes


def _records(
    shortlist: list[tuple[ResearchCandidate, ResearchFeedTriage]],
    review_loader: ReviewLoader,
    profile: Any,
    review_outcomes: dict[str, str] | None = None,
    *, evidence: dict | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for candidate, triage in shortlist:
        if evidence is not None:
            evidence[candidate.source_id] = {"availability": "unknown"}
        failure = (review_outcomes or {}).get(candidate.source_id)
        if failure:
            failed.append({"source_id": candidate.source_id, "error": failure})
        try:
            review = review_loader(candidate.source_id)
            if review is None:
                if not failure:
                    missing.append(candidate.model_dump(mode="json"))
                continue
            if review.get("needs_pdf"):
                missing.append(candidate.model_dump(mode="json"))
                if evidence is not None:
                    evidence[candidate.source_id] = {"availability": "unavailable"}
                raise ValueError("source unavailable: " + str(review.get("acquire_outcome") or "not_observed"))
            _feed_assessment.review_evidence(review)
            if evidence is not None:
                evidence[candidate.source_id] = {"availability": "available", "basis": review["digest"]["basis"]}
            assessment = _feed_assessment.qualify(review, _feed_assessment.context_for(candidate.source_id, profile))
            card = build_card(candidate, triage, review, profile, assessment=assessment)
            records.append({
                "candidate": candidate.model_dump(mode="json"),
                "triage": triage.model_dump(mode="json"),
                "card": card.model_dump(mode="json"),
                "provenance": {
                    "source_id": candidate.source_id,
                    "pipeline_stage": "deep_review_projection",
                    "review_contract_version": review.get("review_contract_version"),
                    "source_binding": review["review_identity"],
                    "triage": {"basis": "stored_abstract_triage", "confidence": triage.confidence},
                    "feed_generation": review.get("feed_assessment"),
                    "project_use_assessment": assessment["status"],
                    "scientific_verification": "unverified",
                    "provider": (review.get("provenance") or {}).get("provider"),
                    "model": (review.get("provenance") or {}).get("model"),
                    "prompt_schema_version": (review.get("provenance") or {}).get("prompt_schema_version"),
                    "generated_at": review.get("reviewed_at"),
                    "basis": (review.get("digest") or {}).get("basis", "unknown"),
                    "confidence": None,
                    "abstained": assessment["status"] != "assessed",
                },
            })
        except Exception as exc:  # noqa: BLE001 - one paper never fails the weekly run.
            if not failure:
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
    review_loader: ReviewLoader | None = None,
) -> dict[str, Any]:
    try:
        if start > end:
            raise ValueError("start must be on or before end")
    except TypeError as exc:
        raise ValueError("start and end must use compatible timezone awareness") from exc
    shortlist_budget, card_budget, source_limit, review_timeout_seconds = parse_run_budgets(
        shortlist_budget, card_budget, source_limit, review_timeout_seconds,
    )
    if review_loader is None:
        review_loader = _feed_assessment.current_review if generate_reviews else _feed_assessment.cached_reader(settings)
    profile, candidates, rows, assessed, assess_failed = _assess(
        settings, start, end, source_limit, venue, generate=generate_reviews,
    )
    included, ranking, ranking_failed = rank_candidates(assessed, rows)
    shortlist = included[:profile.shortlist_budget if shortlist_budget is None else shortlist_budget]
    reviewed = shortlist[:profile.card_budget if card_budget is None else card_budget]
    review_outcomes: dict[str, str] = {}
    if generate_reviews:
        review_outcomes = _ensure_reviews(
            settings, [candidate for candidate, _triage in reviewed], review_timeout_seconds, profile,
        )
    evidence = {}
    records, missing, failed = _records(reviewed, review_loader, profile, review_outcomes, evidence=evidence)
    failed = [*assess_failed, *ranking_failed, *failed]
    for record in records:
        row = rows.get(record["candidate"]["source_id"]) or {}
        record["provenance"]["triage"] = row.get("triage_provenance") or {
            "basis": "stored_abstract_triage", "row_id": row.get("id"), "run_id": row.get("run_id"),
            "model": row.get("model_version"), "generated_at": row.get("created_at"), "currentness": "not_attested"}

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
            "discovered": len(candidates), "deduplicated": len(candidates),
            "triaged": len(assessed), "shortlisted": len(shortlist),
            "full_text_available": sum(value["availability"] == "available" for value in evidence.values()),
            "cards_generated": len(records),
            "failed": len(failed),
        },
        "writebacks": writebacks, "ranking": ranking, "full_text_evidence": evidence,
    }
    payload = _payload(metadata, records, missing, failed, rejects)
    output_dir = settings.data_dir / "research_feed"
    identity = {
        "start": start.isoformat(), "end": end.isoformat(), "venue": venue.casefold().strip(),
        "dry_run": dry_run, "queue_zotero": queue_zotero,
        "generate_reviews": generate_reviews, "source_limit": source_limit,
        "shortlist_budget": shortlist_budget, "card_budget": card_budget,
        "review_timeout_seconds": review_timeout_seconds,
        "profile": profile.model_dump(mode="json"),
    }
    run_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    slug = f"weekly-{end.date().isoformat()}-{run_id}-{uuid.uuid4().hex[:8]}"
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
