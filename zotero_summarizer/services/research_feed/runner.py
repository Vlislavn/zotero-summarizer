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
from zotero_summarizer.services.library import _review_cache
from zotero_summarizer.services.research_feed.card import PROJECTION_CONTRACT, build_card, review_evidence
from zotero_summarizer.services.research_feed.cursor import CandidateCursor, MalformedCursorError
from zotero_summarizer.services.research_feed.profile import load_profile
from zotero_summarizer.services.research_feed.ranking import rank_candidates
from zotero_summarizer.services.research_feed.render import persist
from zotero_summarizer.services.research_feed.source import (
    _number, _summary, admit_history, load_candidates, source_history_metadata,
)
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import feeds as feed_storage


ReviewLoader = Callable[[str], dict[str, Any] | None]
_CANONICAL_CARD = build_card
_CANONICAL_REVIEW_LOADER = _review_cache.get_current_review
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
    summary = _summary(row)
    text = " ".join([candidate.title, candidate.abstract, str(summary.get("executive_summary") or ""),
                     str(summary.get("methods") or "")])
    themes, projects = _matches(profile.themes, text), _matches(profile.projects, text)
    priority_score = {"must_read": 5, "should_read": 4, "could_read": 3, "dont_read": 1}
    raw_score = (row or {}).get("composite_score")
    if raw_score is None:
        raw_score = summary.get("composite_relevance_score")
    raw_score = _number(raw_score, default=1.0, maximum=5.0)
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
        confidence=_number(summary.get("triage_confidence"), default=0.5, maximum=1.0),
        matched_themes=themes, matched_projects=projects,
        contribution_kind=_contributions(text), artifact_signal=artifact,
        novelty_claim=str(method.get("what_is_new") or "") if isinstance(method, dict) else "",
        rationale=reason,
    )


_CANONICAL_TRIAGE = triage_candidate


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
    *, cursor: CandidateCursor | None = None,
) -> tuple[Any, list[ResearchCandidate], dict[str, dict[str, Any]], list[tuple[Any, Any]],
           list[dict[str, str]], dict[str, Any]]:
    profile = load_profile(settings.data_dir)
    candidates = load_candidates(
        settings.triage_db_path, start=start, end=end, limit=source_limit, venue=venue,
    )
    rows = _latest_rows(settings.triage_db_path)
    native_reasons = {
        feed_storage.DECISION_SELECTED: "selected",
        feed_storage.DECISION_USER_REJECTED: "native_veto",
        feed_storage.DECISION_GATE_REJECTED: "native_veto",
        feed_storage.DECISION_REJECTED_LOW_SCORE: "native_veto",
        feed_storage.DECISION_REJECTED_DEDUP_LIBRARY: "duplicate",
        feed_storage.DECISION_REJECTED_DEDUP_PROCESSED: "duplicate",
        feed_storage.DECISION_SKIPPED_ERROR: "skipped_error",
        feed_storage.DECISION_TRIAGED_PENDING: "triage_rule",
        feed_storage.DECISION_AWAITING_REVIEW: "triage_rule",
        feed_storage.DECISION_REJECTED_DAILY_CUTOFF: "triage_rule",
        feed_storage.DECISION_REJECTED_ELBOW: "triage_rule",
        feed_storage.DECISION_USER_APPROVED: "triage_rule",
        feed_storage.DECISION_BLACK_SWAN: "triage_rule",
    }
    assessed, failed = [], []
    effective_candidates, accepted_rows, history = [], {}, {}
    for candidate in candidates:
        row = rows.get(candidate.source_id)
        candidate, entry = admit_history(candidate, row)
        effective_candidates.append(candidate)
        history[candidate.source_id] = entry
        admission = {"status": "failed", "reason": "history_withheld", "native_decision": None}
        entry["admission"] = admission
        try:
            if entry["history"] == "withheld":
                raise ValueError(entry["reason"])
            if row is not None:
                accepted_rows[candidate.source_id] = row
            decision = (row or {}).get("decision")
            native_reason = native_reasons.get(decision) if type(decision) is str else None
            admission.update(reason="triage_history_invalid", native_decision=decision if native_reason else None)
            cached = (cursor.assessment(candidate, row, profile)
                      if cursor is not None and triage_candidate is _CANONICAL_TRIAGE else None)
            triage = cached if cached is not None else triage_candidate(candidate, row, profile)
            admission["reason"] = "no_prior_history" if row is None else native_reason or "invalid_native_decision"
            if admission["reason"] in {"invalid_native_decision", "skipped_error"}:
                raise ValueError(admission["reason"])
            if native_reason in {"selected", "native_veto", "duplicate"}:
                triage = triage.model_copy(update={"include": native_reason == "selected"})
            admission["status"] = "eligible" if triage.include else "excluded"
            if cursor is not None and triage_candidate is _CANONICAL_TRIAGE:
                cursor.remember_triage(candidate.source_id, triage)
            assessed.append((candidate, triage))
        except MalformedCursorError:
            raise
        except Exception as exc:  # noqa: BLE001 — malformed history is isolated per paper.
            reason = entry["reason"] if admission["reason"] == "history_withheld" else admission["reason"]
            LOGGER.warning("research-feed triage failed source=%s: %s", candidate.source_id, reason)
            failed.append({"source_id": candidate.source_id, "error": f"{type(exc).__name__}: {reason}"})
    return profile, effective_candidates, accepted_rows, assessed, failed, history


def _ensure_reviews(
    settings: Settings, candidates: list[ResearchCandidate], timeout_seconds: int, profile: Any = None,
) -> dict[str, str]:
    """Run reviews for cache misses and return per-key timeout/error outcomes."""
    from zotero_summarizer.services.library import deep_review
    from zotero_summarizer.services.library.app_library_reader import AppLibraryReader

    contexts = {}
    if profile is not None:
        from zotero_summarizer.services.library import _feed_assessment

        contexts = {candidate.source_id: _feed_assessment.context_for(candidate.source_id, profile)
                    for candidate in candidates}
    keys = []
    for candidate in candidates:
        review = deep_review.get_current_review(candidate.source_id)
        if review is None or (profile is not None and (not review_evidence(review)["qualified"]
                or not _feed_assessment.qualify(review, contexts[candidate.source_id])["qualified"])):
            keys.append(candidate.source_id)
    if not keys:
        return {}
    extra = {"feed_contexts": {key: contexts[key] for key in keys}} if profile is not None else {}
    try:
        deep_review.start(
            item_keys=keys, reader=AppLibraryReader(settings.triage_db_path), acquire_missing=True, **extra,
        )
    except Exception as exc:  # noqa: BLE001 — report start failure per affected paper.
        return {key: f"deep review start failed: {type(exc).__name__}: {exc}" for key in keys}
    deadline = time.monotonic() + timeout_seconds
    pending = set(keys)
    outcomes = {}
    while pending:
        for key in tuple(pending):
            job = deep_review.status(key)
            if job.get("status") == "running":
                continue
            pending.remove(key)
            if job.get("status") == "error":
                outcomes[key] = str(job.get("error") or "deep review failed")
            elif profile is not None:
                current = deep_review.get_current_review(key)
                if (not review_evidence(current)["qualified"]
                        or not _feed_assessment.qualify(current, contexts[key])["qualified"]):
                    outcomes[key] = "deep review did not produce a current feed assessment"
        if not pending:
            break
        if time.monotonic() >= deadline:
            for key in pending:
                outcomes[key] = f"deep review timed out after {timeout_seconds}s"
            break
        time.sleep(0.2)
    return outcomes


def _provenance(candidate: ResearchCandidate, review: dict[str, Any], profile: Any) -> dict[str, Any]:
    from zotero_summarizer.services.library import _feed_assessment

    inherited = review.get("provenance") or {}
    assessment = _feed_assessment.qualify(review, _feed_assessment.context_for(candidate.source_id, profile))
    result = {
        "source_id": candidate.source_id, "pipeline_stage": "deep_review_projection",
        "projection_contract": PROJECTION_CONTRACT,
        "review_contract_version": review.get("review_contract_version"),
        "source_binding": review["review_identity"],
        "provider": inherited.get("provider"), "model": inherited.get("model"),
        "prompt_schema_version": inherited.get("prompt_schema_version"),
        "generated_at": review.get("reviewed_at"), "basis": review["digest"]["basis"],
        "confidence": None, "abstained": True,
        "abstention_scope": ["project_uses", "research_ideas"],
        "project_use_assessment": assessment["status"], "project_use_reason": assessment["reason"],
    }
    if assessment["qualified"]:
        result.update(abstained=assessment["status"] != "assessed",
                      abstention_scope=["unassessed_projects"] if assessment["status"] != "assessed" else [],
                      feed_generation=assessment["producer"], assessment_origin="ai_generated",
                      scientific_verification="unverified")
    return result


def _records(
    shortlist: list[tuple[ResearchCandidate, ResearchFeedTriage]],
    review_loader: ReviewLoader,
    profile: Any,
    review_outcomes: dict[str, str] | None = None,
    *, cursor: CandidateCursor | None = None,
) -> tuple[list, list, list, list, dict]:
    records, missing, failed, unqualified = [], [], [], []
    evidence = {}
    reuse = (cursor is not None and build_card is _CANONICAL_CARD
             and review_loader is _CANONICAL_REVIEW_LOADER)
    for candidate, triage in shortlist:
        evidence[candidate.source_id] = review_evidence(None)
        try:
            review = review_loader(candidate.source_id)
            observed = review_evidence(review)
            evidence[candidate.source_id] = observed
            if not observed["qualified"]:
                failure = (review_outcomes or {}).get(candidate.source_id)
                if failure:
                    failed.append({"source_id": candidate.source_id, "error": failure})
                if cursor is not None:
                    cursor.discard_record(candidate.source_id)
                body = candidate.model_dump(mode="json")
                if observed["availability"] == "unavailable":
                    missing.append(body)
                else:
                    unqualified.append({"candidate": body, "reason": observed["reason"]})
                continue
            if reuse:
                record = cursor.record(candidate, triage, review)
                if record is not None:
                    records.append(record)
                    continue
            card = build_card(candidate, triage, review, profile)
            if candidate.source_id in (review_outcomes or {}):
                failed.append({"source_id": candidate.source_id, "error": review_outcomes[candidate.source_id]})
            records.append({
                "candidate": candidate.model_dump(mode="json"),
                "triage": triage.model_dump(mode="json"),
                "card": card.model_dump(mode="json"),
                "provenance": _provenance(candidate, review, profile),
            })
            if reuse:
                cursor.remember_record(candidate.source_id, records[-1])
        except Exception as exc:  # noqa: BLE001 - one paper never fails the weekly run.
            if evidence[candidate.source_id]["reason"] == "review_missing":
                evidence[candidate.source_id]["reason"] = "review_load_failed"
            failed.append({"source_id": candidate.source_id, "error": f"{type(exc).__name__}: {exc}"})
    return records, missing, failed, unqualified, evidence


def _payload(
    metadata: dict[str, Any], records: list[dict[str, Any]], missing: list[dict[str, Any]],
    failed: list[dict[str, str]], rejects: list[tuple[Any, Any]], unqualified: list[dict[str, Any]],
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
        "unqualified_reviews": unqualified,
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
    try:
        if start > end:
            raise ValueError("start must be on or before end")
    except TypeError as exc:
        raise ValueError("start and end must use compatible timezone awareness") from exc
    shortlist_budget, card_budget, source_limit, review_timeout_seconds = parse_run_budgets(
        shortlist_budget, card_budget, source_limit, review_timeout_seconds,
    )
    cursor = CandidateCursor(settings)
    profile, candidates, rows, assessed, assess_failed, history = _assess(
        settings, start, end, source_limit, venue, cursor=cursor,
    )
    included, ranking, ranking_failed = rank_candidates(assessed, rows, list(history))
    shortlist = included[:profile.shortlist_budget if shortlist_budget is None else shortlist_budget]
    reviewed = shortlist[:profile.card_budget if card_budget is None else card_budget]
    review_outcomes: dict[str, str] = {}
    if generate_reviews:
        review_outcomes = _ensure_reviews(
            settings, [candidate for candidate, _triage in reviewed], review_timeout_seconds, profile,
        )
    records, missing, failed, unqualified, evidence = _records(
        reviewed, review_loader, profile, review_outcomes, cursor=cursor,
    )
    failed = [*assess_failed, *ranking_failed, *failed]
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
            "full_text_available": sum(row["availability"] == "available" for row in evidence.values()),
            "cards_generated": len(records),
            "failed": len(failed),
        },
        "writebacks": writebacks,
        "source_history": source_history_metadata(candidates, history, records, missing, rejects, unqualified),
        "full_text_evidence": evidence,
        "full_text_evidence_scope": "card_budget_candidates",
        "ranking": ranking,
        "candidate_cursor": cursor.metadata,
    }
    payload = _payload(metadata, records, missing, failed, rejects, unqualified)
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
        "candidate_cursor": cursor.checkpoint(slug, records),
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
