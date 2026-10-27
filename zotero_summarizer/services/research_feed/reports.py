"""Read-only access to the existing publisher's immutable RSS/Telegram bundles."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import ResearchCandidate, ResearchEngineeringCard, ResearchFeedTriage
from zotero_summarizer.models.research_feed import ResearchIdea
from zotero_summarizer.settings import Settings


class _SourceBinding(BaseModel):
    source_kind: Literal["library", "override"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _CardSourceBinding(_SourceBinding):
    generation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _FullTextEvidence(BaseModel):
    source_binding: _SourceBinding | None = None


class _FeedGeneration(BaseModel):
    status: Literal["assessed", "partial"]
    origin: Literal["ai_generated"]
    scientific_verification: Literal["unverified"]
    retained_payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _receipt_hash(self):
        if not (self.retained_payload_sha256 or self.payload_sha256):
            raise ValueError("feed generation requires a retained receipt hash")
        return self


class _Provenance(BaseModel):
    basis: str | None = None
    model: str | None = None
    generated_at: str | None = None
    source_binding: _CardSourceBinding | None = None
    feed_generation: _FeedGeneration | None = None


class _Metadata(BaseModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    source: Literal["app_rss"]
    start: str = Field(alias="from")
    to: str
    generated_at: str | None = None
    counts: dict[str, Annotated[int, Field(strict=True, ge=0)]]
    full_text_evidence: dict[str, _FullTextEvidence] | None = None

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


class _Selection(BaseModel):
    candidate: ResearchCandidate
    triage: ResearchFeedTriage


class _Card(_Selection):
    card: ResearchEngineeringCard
    provenance: _Provenance = Field(default_factory=_Provenance)


class _Failure(BaseModel):
    source_id: str
    error: str


class _Unqualified(BaseModel):
    candidate: ResearchCandidate
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review qualification reason is blank")
        return value


class _Report(BaseModel):
    metadata: _Metadata
    cards: list[_Card]
    rejected_near_threshold: list[_Selection]
    manual_full_text_required: list[ResearchCandidate]
    failed: list[_Failure]
    unqualified_reviews: list[_Unqualified] = Field(default_factory=list)
    research_ideas_by_project: dict[str, list[ResearchIdea]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _card_count(self):
        if self.metadata.counts["cards_generated"] != len(self.cards):
            raise ValueError("card count does not match saved cards")
        return self


def _telegram_identity(meta, batches):
    identity = meta.get("evaluation_identity")
    if not isinstance(identity, dict):
        raise ValueError("generated topics require processing identity")
    if (identity.get("source_hashes") != [batch.content_hash for batch in batches]
            or identity.get("limits") != meta["limits"] or identity.get("local_only") is not True
            or identity.get("scientific_verification") != "unverified"):
        raise ValueError("Telegram review identity differs from source snapshot")
    for key in ("prompt_sha256", "schema_sha256", "processing_identity"):
        if not isinstance(identity.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", identity[key]):
            raise ValueError("invalid Telegram review fingerprint")
    for key in ("provider", "model", "prompt_version"):
        if not isinstance(identity.get(key), str) or not identity[key].strip():
            raise ValueError("missing Telegram review identity")


def _validate_telegram(payload):
    from zotero_summarizer.models.research_feed import TelegramPost, TelegramNewsVerdict, TelegramLimits
    from zotero_summarizer.models.telegram_discussion import TelegramConversation, source_records
    from zotero_summarizer.services.research_feed.telegram_review import validate_topics, admitted_sources

    if not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict):
        raise ValueError("invalid Telegram report")
    meta = payload["metadata"]
    if type(meta.get("schema_version")) is not int or meta.get("source") != "telegram":
        raise ValueError("invalid Telegram metadata")
    counts = meta.get("counts")
    if (not isinstance(counts, dict) or any(type(value) is not int or value < 0 for value in counts.values())
            or "cards_generated" not in counts):
        raise ValueError("invalid Telegram counts")
    if meta["schema_version"] == 2:
        TelegramLimits.model_validate(meta["limits"])
        batches = [TelegramConversation.model_validate(value) for value in payload["conversations"]]
        topics, errors = payload["topics"], payload["errors"]
        if not isinstance(topics, list) or not isinstance(errors, list):
            raise ValueError("invalid Telegram topics/errors")
        if topics:
            validate_topics({"topics": topics}, batches)
            _telegram_identity(meta, batches)
        elif admitted_sources(batches) and meta["mode"] == "review" and not errors:
            raise ValueError("nonempty review requires topics or errors")
        if (counts["cards_generated"] != len(topics) or counts["conversations"] != len(batches)
                or counts["messages"] != len(source_records(batches)) or counts["errors"] != len(errors)):
            raise ValueError("Telegram report counts differ")
        return
    posts = payload["posts"]
    if not isinstance(posts, list) or not isinstance(payload["cards"], list) or not isinstance(payload["uncertain"], list):
        raise ValueError("invalid saved-news report")
    records = {}
    for record in posts:
        original = TelegramPost.model_validate(record["post"])
        if record["source_id"] != original.source_id or original.source_id in records:
            raise ValueError("invalid saved-news identity")
        if record["state"] not in {"keep", "drop", "uncertain", "pending", "error", "skipped"}:
            raise ValueError("invalid saved-news state")
        if record["state"] in {"keep", "drop", "uncertain"}:
            verdict = TelegramNewsVerdict.model_validate(record["verdict"])
            if verdict.decision != record["state"] or verdict.evidence_quote not in original.text:
                raise ValueError("saved-news verdict differs from original")
        records[original.source_id] = record
    if len(payload["cards"]) != counts["cards_generated"]:
        raise ValueError("saved-news cards count differs")
    for card in payload["cards"]:
        record = records[card["source_id"]]
        expected = {"source_id": record["source_id"], "permalink": record["post"]["permalink"], **record["verdict"]}
        if record["state"] != "keep" or card != expected:
            raise ValueError("saved-news card differs from retained verdict")


def report_path(settings: Settings, run_id: str, extension: str = "json") -> Path:
    root = settings.data_dir / "research_feed"
    if not re.fullmatch(r"(?:weekly-|saved-news-|telegram-review-)[A-Za-z0-9_.-]+", run_id) or extension not in {"json", "md"}:
        raise APIError(error="not_found", message="Weekly digest not found", status_code=404)
    owner = root if run_id.startswith("weekly-") else root / "telegram"
    bundle = owner / run_id
    path = bundle / f"{run_id}.{extension}"
    if (root.is_symlink() or owner.is_symlink() or bundle.is_symlink() or path.is_symlink() or not path.is_file()
            or path.resolve().parent.parent != owner.resolve()):
        raise APIError(error="not_found", message="Weekly digest not found", status_code=404)
    return path


def _finite_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _read_validated_report(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        payload = json.loads(text, parse_float=_finite_number, parse_constant=_finite_number)
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        expected_source = "app_rss" if path.parent.name.startswith("weekly-") else "telegram"
        versions = {1} if expected_source == "app_rss" else {1, 2}
        if isinstance(metadata, dict) and (
            (type(metadata.get("schema_version")) is int and metadata["schema_version"] not in versions)
            or (isinstance(metadata.get("source"), str) and metadata["source"] != expected_source)
        ):
            raise APIError(error="report_unsupported", message="Saved weekly digest format is unsupported",
                           status_code=422)
        if expected_source == "app_rss":
            _Report.model_validate_json(raw, strict=True)
        else:
            _validate_telegram(payload)
        return raw, payload
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise APIError(error="report_unavailable", message="Saved weekly digest is unreadable or invalid",
                       status_code=422) from exc


def get_report(settings: Settings, run_id: str) -> dict[str, Any]:
    path = report_path(settings, run_id)
    # Return saved fields verbatim, not defaults or a migrated projection.
    return _read_validated_report(path)[1]


def download_bytes(settings: Settings, run_id: str, extension: str) -> bytes:
    if extension not in {"json", "md"}:
        raise APIError(error="not_found", message="Weekly digest not found", status_code=404)
    json_path = report_path(settings, run_id)
    raw, _ = _read_validated_report(json_path)
    if extension == "json":
        return raw
    markdown_path = report_path(settings, run_id, extension)
    try:
        return markdown_path.read_bytes()
    except OSError as exc:
        raise APIError(error="not_found", message="Weekly digest not found", status_code=404) from exc


def _publication_time(bundle: Path, generated_at=None) -> float:
    if isinstance(generated_at, str):
        try:
            parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            if parsed.utcoffset() is not None:
                return parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    # Unavailable/undated bundles remain discoverable, without following links.
    try:
        return bundle.lstat().st_mtime
    except OSError:
        return float("-inf")


def _listed_report(settings, bundle):
    entry = {"run_id": bundle.name, "available": False}
    try:
        payload = get_report(settings, bundle.name)
    except APIError:
        return _publication_time(bundle), entry
    meta = payload["metadata"]
    entry.update(available=True, source=meta["source"], start=meta.get("from"), end=meta.get("to"),
                 generated_at=meta.get("generated_at"), counts=meta["counts"])
    return _publication_time(bundle, meta.get("generated_at")), entry


def list_reports(settings: Settings, *, limit: int = 50) -> dict[str, Any]:
    root = settings.data_dir / "research_feed"
    try:
        if root.is_symlink():
            raise APIError(error="reports_unavailable", message="Saved weekly digests are unavailable", status_code=503)
        bundles = [path for path in root.iterdir() if path.name.startswith("weekly-")]
        telegram_root = root / "telegram"
        if telegram_root.is_symlink():
            raise APIError(error="reports_unavailable", message="Saved Telegram digests are unavailable", status_code=503)
        if telegram_root.exists():
            bundles.extend(path for path in telegram_root.iterdir()
                           if path.name.startswith(("saved-news-", "telegram-review-")))
    except FileNotFoundError:
        return {"items": []}
    except OSError as exc:
        raise APIError(error="reports_unavailable", message="Saved weekly digests are unavailable",
                       status_code=503) from exc
    # Merge all sources before applying the shared limit. Retain only small list
    # entries, not every report body; bundle names break equal-time ties only.
    observed = [_listed_report(settings, bundle) for bundle in bundles]
    observed.sort(key=lambda value: (value[0], value[1]["run_id"]), reverse=True)
    return {"items": [entry for _time, entry in observed[:limit]]}
