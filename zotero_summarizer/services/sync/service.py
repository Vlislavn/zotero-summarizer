"""Paper snapshots plus ordered, field-conflict-aware offline mutations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from zotero_summarizer.domain import READING_PRIORITY_SORT_RANK
from zotero_summarizer.services.golden import label_verdicts, verdict_effects
from zotero_summarizer.services.library import deep_review, reading_queue
from zotero_summarizer.storage import repositories

_PAPER_FIELDS = (
    "item_key",
    "title",
    "authors",
    "venue",
    "reading_priority",
    "date_added",
    "publication_date",
    "year",
    "abstract_preview",
    "has_pdf",
    "relevance_score",
    "why_reason",
    "quality_grade",
    "quality_band",
    "proposed_verdict",
)
LOGGER = logging.getLogger(__name__)


def _paper_snapshots(db_path: Path) -> list[dict[str, Any]]:
    queue = reading_queue.build_reading_queue(
        include_read=True,
        limit=500,
        include_abstract=True,
    )
    papers = {
        row["item_key"]: {key: row.get(key) for key in _PAPER_FIELDS}
        for row in queue.get("items", [])
    }
    fields = repositories.sync_current_fields(db_path)
    reviews = deep_review._read_all()
    for item_key, _field in fields:
        papers.setdefault(item_key, {"item_key": item_key, "title": item_key})
    for item_key, paper in papers.items():
        verdict = fields.get((item_key, "verdict"))
        note = fields.get((item_key, "review_note"))
        paper["verdict"] = (
            None
            if verdict is None or verdict["value"] is None
            else {
                "user_priority": verdict["value"],
                "comment": verdict["comment"],
                "source": verdict["source"],
            }
        )
        paper["review_note"] = (
            None if note is None or note["value"] is None else note["value"]
        )
        paper["model_priority"] = (
            verdict.get("model_priority") if verdict else None
        ) or paper.get("reading_priority")
        digest = (reviews.get(item_key) or {}).get("digest") or {}
        paper["review_digest"] = (
            {
                "tldr": str(digest.get("tldr") or "")[:500],
                "executive_summary": str(digest.get("executive_summary") or "")[:1500],
                "key_findings": [
                    str(v)[:500] for v in (digest.get("key_findings") or [])[:3]
                ],
            }
            if digest
            else None
        )
        paper["revisions"] = {
            field: (fields.get((item_key, field)) or {}).get("revision", 0)
            for field in ("verdict", "review_note")
        }
    return list(papers.values())


def pull(db_path: Path, since: int) -> dict[str, Any]:
    if since < 0:
        raise ValueError("since must be non-negative")
    delta = repositories.pull_sync_changes(db_path, since)
    return {"protocol": 1, **delta, "papers": _paper_snapshots(db_path)}


def _validate_mutation(mutation: dict[str, Any]) -> None:
    if mutation["operation"] == "set" and mutation["field"] == "verdict":
        if mutation.get("value") not in READING_PRIORITY_SORT_RANK:
            raise ValueError("verdict value must be a reading priority")
    if mutation["operation"] == "set" and mutation["field"] == "review_note":
        if not isinstance(mutation.get("value"), str):
            raise ValueError("review_note value must be a string")


def _log_applied_verdict(mutation: dict[str, Any], result: dict[str, Any]) -> None:
    if result["status"] != "applied":
        return
    previous = result["previous"]
    current = result["canonical"]
    label_verdicts.log_committed_transition(
        item_key=mutation["item_key"],
        previous=previous,
        new_user_priority=current["value"],
        model_priority=previous.get("model_priority") or mutation.get("model_priority"),
        surface="offline_sync",
        source=(
            "sync_conflict_resolution"
            if mutation.get("resolves_mutation_id")
            else "offline_sync"
        ),
        comment=mutation.get("comment") or "",
    )


def _run_post_commit_effects(mutation: dict[str, Any], result: dict[str, Any]) -> None:
    if result["status"] not in {"applied", "already_applied"}:
        return
    if mutation["operation"] != "set":
        return
    try:
        if mutation["field"] == "verdict":
            verdict_effects.apply_verdict_effects(
                mutation["item_key"],
                mutation["value"],
                mutation.get("comment") or "",
            )
        else:
            verdict_effects.mirror_review_note(
                mutation["item_key"],
                mutation.get("value") or "",
            )
    except Exception as exc:  # noqa: BLE001 - current state already committed
        LOGGER.warning(
            "sync post-commit effects for %s failed: %s", mutation["item_key"], exc
        )


def push(db_path: Path, mutations: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    last_applied: dict[tuple[str, str, str], int] = {}
    for mutation in mutations:
        key = (mutation["device_id"], mutation["item_key"], mutation["field"])
        effective = dict(mutation)
        if key in last_applied and effective["base_revision"] < last_applied[key]:
            effective["_effective_base_revision"] = last_applied[key]
        try:
            _validate_mutation(effective)
            result = repositories.apply_sync_mutation(db_path, effective)
            if effective["field"] == "verdict":
                _log_applied_verdict(effective, result)
            _run_post_commit_effects(effective, result)
            if result["status"] in {"applied", "already_applied"}:
                last_applied[key] = result["applied_revision"]
        except ValueError as exc:
            result = {
                "mutation_id": mutation["mutation_id"],
                "status": "rejected",
                "error": str(exc),
            }
        results.append(result)
    return {"protocol": 1, "results": results, **repositories.sync_status(db_path)}


def status(db_path: Path) -> dict[str, Any]:
    return {"protocol": 1, **repositories.sync_status(db_path), "server": "canonical"}
