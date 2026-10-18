"""Read-only access to the existing publisher's immutable weekly RSS bundles."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import ResearchCandidate, ResearchEngineeringCard, ResearchFeedTriage
from zotero_summarizer.models.research_feed import ResearchIdea
from zotero_summarizer.settings import Settings


class _Metadata(BaseModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    source: Literal["app_rss"]
    start: str = Field(alias="from")
    to: str
    generated_at: str | None = None
    counts: dict[str, Annotated[int, Field(strict=True, ge=0)]]

    @field_validator("start", "to")
    @classmethod
    def _date(cls, value: str) -> str:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @field_validator("counts")
    @classmethod
    def _required_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if not {"discovered", "shortlisted", "cards_generated"} <= value.keys():
            raise ValueError("missing report counts")
        return value


class _Provenance(BaseModel):
    basis: str | None = None
    model: str | None = None
    generated_at: str | None = None


class _Selection(BaseModel):
    candidate: ResearchCandidate
    triage: ResearchFeedTriage


class _Card(_Selection):
    card: ResearchEngineeringCard
    provenance: _Provenance = Field(default_factory=_Provenance)


class _Failure(BaseModel):
    source_id: str
    error: str


class _Report(BaseModel):
    metadata: _Metadata
    cards: list[_Card]
    rejected_near_threshold: list[_Selection]
    manual_full_text_required: list[ResearchCandidate]
    failed: list[_Failure]
    research_ideas_by_project: dict[str, list[ResearchIdea]] = Field(default_factory=dict)


def report_path(settings: Settings, run_id: str, extension: str = "json") -> Path:
    root = settings.data_dir / "research_feed"
    if not re.fullmatch(r"weekly-[A-Za-z0-9_.-]+", run_id) or extension not in {"json", "md"}:
        raise APIError(error="not_found", message="Weekly digest not found", status_code=404)
    bundle = root / run_id
    path = bundle / f"{run_id}.{extension}"
    if (root.is_symlink() or bundle.is_symlink() or path.is_symlink() or not path.is_file()
            or path.resolve().parent.parent != root.resolve()):
        raise APIError(error="not_found", message="Weekly digest not found", status_code=404)
    return path


def _finite_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def get_report(settings: Settings, run_id: str) -> dict[str, Any]:
    path = report_path(settings, run_id)
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text, parse_float=_finite_number, parse_constant=_finite_number)
        _Report.model_validate_json(text, strict=True)
        # Return the saved fields verbatim, not defaults or a migrated projection.
        return payload
    except (OSError, ValueError) as exc:
        raise APIError(error="report_unavailable", message="Saved weekly digest is unreadable or unsupported",
                       status_code=422) from exc


def list_reports(settings: Settings, *, limit: int = 50) -> dict[str, Any]:
    root = settings.data_dir / "research_feed"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise APIError(error="reports_unavailable", message="Saved weekly digests are unavailable", status_code=503)
    try:
        bundles = sorted(root.glob("weekly-*"), key=lambda path: path.name, reverse=True)
    except OSError as exc:
        raise APIError(error="reports_unavailable", message="Saved weekly digests are unavailable",
                       status_code=503) from exc
    items = []
    for bundle in bundles[:limit]:
        entry = {"run_id": bundle.name, "available": False}
        try:
            payload = get_report(settings, bundle.name)
        except APIError:
            items.append(entry)
            continue
        meta = payload["metadata"]
        entry.update(available=True, start=meta["from"], end=meta["to"],
                     generated_at=meta.get("generated_at"), counts=meta["counts"])
        items.append(entry)
    return {"items": items}
