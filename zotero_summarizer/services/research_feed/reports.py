"""Browse immutable RSS weekly bundles and explicitly project cached reviews."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import ResearchCandidate, ResearchEngineeringCard, ResearchFeedTriage, ResearchIdea
from zotero_summarizer.services.research_feed.runner import run_weekly
from zotero_summarizer.settings import Settings


def valid_run_id(run_id: object) -> bool:
    return (type(run_id) is str and run_id.startswith("weekly-")
            and all(char.isascii() and (char.isalnum() or char in "-_.") for char in run_id))


def report_path(settings: Settings, run_id: str, extension: str = "json") -> Path:
    root = settings.data_dir / "research_feed"
    if not valid_run_id(run_id) or extension not in {"json", "md"}:
        raise APIError(error="not_found", message="Weekly digest not found", status_code=404)
    bundle = root / run_id
    path = bundle / f"{run_id}.{extension}"
    if (root.is_symlink() or bundle.is_symlink() or path.is_symlink() or not path.is_file()
            or path.resolve().parent.parent != root.resolve()):
        raise APIError(error="not_found", message="Weekly digest not found", status_code=404)
    return path


def get_report(settings: Settings, run_id: str) -> dict[str, Any]:
    path = report_path(settings, run_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        meta = payload["metadata"]
        if (type(meta["schema_version"]) is not int or meta["schema_version"] != 1 or meta["source"] != "app_rss"
                or not isinstance(meta["counts"], dict)
                or not all(isinstance(payload[key], list) for key in (
                    "cards", "rejected_near_threshold", "manual_full_text_required", "failed",
                ))):
            raise ValueError("unsupported report")
        for record in payload["cards"]:
            ResearchCandidate.model_validate(record["candidate"])
            ResearchFeedTriage.model_validate(record["triage"])
            ResearchEngineeringCard.model_validate(record["card"])
            provenance = record.get("provenance", {})
            if (not isinstance(provenance, dict) or any(
                    provenance.get(key) is not None and not isinstance(provenance[key], str)
                    for key in ("basis", "model", "generated_at"))):
                raise ValueError("unsupported provenance")
        for record in payload["rejected_near_threshold"]:
            ResearchCandidate.model_validate(record["candidate"])
            ResearchFeedTriage.model_validate(record["triage"])
        for candidate in payload["manual_full_text_required"]:
            ResearchCandidate.model_validate(candidate)
        idea_groups = payload.get("research_ideas_by_project", {})
        if not isinstance(idea_groups, dict) or any(
                not isinstance(ideas, list) for ideas in idea_groups.values()):
            raise ValueError("unsupported research idea groups")
        for ideas in idea_groups.values():
            for idea in ideas:
                ResearchIdea.model_validate(idea)
        if (any(not isinstance(meta.get(key), str) for key in ("from", "to"))
                or not {"discovered", "shortlisted", "cards_generated"} <= meta["counts"].keys()
                or any(type(value) is not int for value in meta["counts"].values())
                or any(not isinstance(row.get("source_id"), str) or not isinstance(row.get("error"), str)
                       for row in payload["failed"])):
            raise ValueError("unsupported counts or failures")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise APIError(error="report_unavailable", message="Saved weekly digest is unreadable or unsupported",
                       status_code=422) from exc
    return payload


def list_reports(settings: Settings, *, limit: int = 50) -> dict[str, Any]:
    root = settings.data_dir / "research_feed"
    items = []
    bundles = sorted(root.glob("weekly-*"), key=lambda path: path.name, reverse=True)
    for bundle in bundles[:limit]:
        entry = {"run_id": bundle.name, "available": False}
        try:
            payload = get_report(settings, bundle.name)
        except APIError:
            items.append(entry)
            continue
        meta = payload["metadata"]
        entry.update(available=True, start=meta.get("from"), end=meta.get("to"),
                     generated_at=meta.get("generated_at"), counts=meta["counts"])
        items.append(entry)
    return {"items": items}


def run_cached(
    settings: Settings, *, start: date, end: date, venue: str = "", card_budget: int = 10,
) -> dict[str, Any]:
    if start > end:
        raise APIError(error="invalid_window", message="Start must be on or before end", status_code=422)
    if not settings.triage_db_path.is_file():
        raise APIError(error="source_unavailable", message="Refresh and triage RSS sources in Today first",
                       status_code=409)
    try:
        result = run_weekly(
            settings, start=datetime.combine(start, time.min, timezone.utc),
            end=datetime.combine(end, time.max, timezone.utc), venue=venue,
            card_budget=card_budget, generate_reviews=False, dry_run=True, queue_zotero=False,
        )
    except ValueError as exc:
        raise APIError(error="invalid_source", message="Research profile, saved source data or candidate cursor is invalid; inspect the weekly CLI diagnostics",
                       status_code=422) from exc
    except (sqlite3.Error, OSError) as exc:
        raise APIError(error="source_unavailable", message="Weekly sources or report storage are unavailable",
                       status_code=503) from exc
    return {"run_id": Path(result["json_path"]).parent.name,
            "cards_generated": result["cards_generated"], "failed": result["failed"]}
