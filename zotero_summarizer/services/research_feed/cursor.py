"""Bounded successful-stage replay owned by the weekly runner's state pointer."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import ResearchEngineeringCard, ResearchFeedTriage
from zotero_summarizer.services._common import read_json_or_empty
from zotero_summarizer.services.library._digest_audit import canonical_hash

# Same ceiling as parse_run_budgets' existing source-row admission, not a new knob.
MAX_ENTRIES = 5000


def stage_identity() -> str:
    from zotero_summarizer.services.library._feed_assessment import _contract_identity

    root = Path(__file__).parents[2]
    sources = (
        "services/research_feed/runner.py", "services/research_feed/source.py",
        "services/research_feed/card.py", "services/research_feed/cursor.py",
        "models/research_feed.py", "services/library/review_fleet/propose.py",
        "services/library/_review_cache.py", "services/library/_digest_audit.py",
    )
    return canonical_hash({
        "sources": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources},
        "triage_schema": ResearchFeedTriage.model_json_schema(),
        "card_schema": ResearchEngineeringCard.model_json_schema(),
        "feed_producer": _contract_identity(),
    })


class MalformedCursorError(ValueError):
    """Invalid persisted replay state, not a failed scientific paper assessment."""


def _malformed() -> None:
    raise MalformedCursorError("Malformed weekly candidate cursor")


def _sha(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _validate_entry(key: str, entry: Any) -> None:
    from zotero_summarizer.services.research_feed.reports import valid_run_id

    if (type(key) is not str or not key or type(entry) is not dict
            or set(entry) != {"input_sha256", "triage", "triage_sha256", "card"}
            or not _sha(entry["input_sha256"]) or not _sha(entry["triage_sha256"])):
        _malformed()
    triage = entry["triage"]
    if type(triage) is not dict:
        _malformed()
    if canonical_hash(triage) != entry["triage_sha256"]:
        _malformed()
    card = entry["card"]
    if card is not None and (
            type(card) is not dict or set(card) != {"run_id", "index", "review_sha256", "record_sha256"}
            or not valid_run_id(card["run_id"]) or type(card["index"]) is not int or not 0 <= card["index"] < 20
            or not _sha(card["review_sha256"]) or not _sha(card["record_sha256"])):
        _malformed()


def _read_entries(settings) -> tuple[dict, str]:
    path = settings.data_dir / "research_feed" / "state.json"
    if path.parent.is_symlink() or path.is_symlink() or path.exists() and not path.is_file():
        _malformed()
    state = read_json_or_empty(path)
    if (type(state) is not dict or "schema_version" in state
            and (type(state["schema_version"]) is not int or state["schema_version"] != 1)):
        _malformed()
    if "candidate_cursor" not in state:
        return {}, "legacy" if path.exists() else "absent"
    cursor = state["candidate_cursor"]
    if (type(cursor) is not dict or set(cursor) != {"schema_version", "entries"}
            or type(cursor["schema_version"]) is not int or cursor["schema_version"] != 1
            or type(cursor["entries"]) is not dict or len(cursor["entries"]) > MAX_ENTRIES):
        _malformed()
    for key, entry in cursor["entries"].items():
        _validate_entry(key, entry)
    return cursor["entries"], "available"


class CandidateCursor:
    def __init__(self, settings):
        self.settings = settings
        self.entries, prior = _read_entries(settings)
        self.previous = dict(self.entries)
        self.contract = stage_identity()
        self.inputs: dict[str, str] = {}
        self.reviews: dict[str, str] = {}
        self.pending: dict[str, str] = {}
        self.metadata = {"schema_version": 1, "prior": prior, "triage_reused": 0,
                         "cards_reused": 0, "issues": {}}
        self.reports: dict[str, dict] = {}

    def assessment(self, candidate, row, profile):
        key = candidate.source_id
        try:
            identity = canonical_hash({"candidate": candidate.model_dump(mode="json"), "row": row,
                                       "profile": profile.model_dump(mode="json"), "stage": self.contract})
        except (TypeError, ValueError):
            self.metadata["issues"][key] = "input_identity_unavailable"
            return None
        self.inputs[key] = identity
        entry = self.entries.get(key) or self.previous.get(key)
        if entry is None:
            return None
        if entry["input_sha256"] != identity:
            self.metadata["issues"][key] = "input_changed"
            return None
        if entry["triage"].keys() != ResearchFeedTriage.model_fields.keys():
            _malformed()
        try:
            triage = ResearchFeedTriage.model_validate(entry["triage"], strict=True)
        except ValueError:
            _malformed()
        self.metadata["triage_reused"] += 1
        return triage

    def remember_triage(self, key: str, triage: ResearchFeedTriage) -> None:
        identity = self.inputs.get(key)
        if identity is None:
            return
        previous = self.entries.pop(key, None) or self.previous.get(key)
        raw = triage.model_dump(mode="json")
        unchanged = previous and previous["input_sha256"] == identity and previous["triage"] == raw
        self.entries[key] = {"input_sha256": identity, "triage": raw, "triage_sha256": canonical_hash(raw),
                             "card": previous["card"] if unchanged else None}
        while len(self.entries) > MAX_ENTRIES:
            del self.entries[next(iter(self.entries))]

    def discard_record(self, key: str) -> None:
        if key in self.entries:
            self.entries[key]["card"] = None
        self.pending.pop(key, None)

    def record(self, candidate, triage, review):
        from zotero_summarizer.services.research_feed.card import review_evidence

        key = candidate.source_id
        if not review_evidence(review)["qualified"]:
            self.discard_record(key)
            self.metadata["issues"][key] = "review_unqualified"
            return None
        entry = self.entries.get(key)
        if key not in self.inputs or entry is None or entry["input_sha256"] != self.inputs[key]:
            return None
        # Caller has just obtained this artifact from the canonical current-review reader.
        identity = canonical_hash({"review": review, "triage": triage.model_dump(mode="json"),
                                   "input": self.inputs[key]})
        self.reviews[key] = identity
        reference = entry["card"]
        if reference is None:
            return None
        if reference["review_sha256"] != identity:
            self.metadata["issues"][key] = "review_changed"
            return None
        record = self._record_from_report(key, reference)
        if record is None:
            return None
        from zotero_summarizer.services.research_feed.card import PROJECTION_CONTRACT

        provenance = record.get("provenance") or {}
        if (provenance.get("projection_contract") != PROJECTION_CONTRACT
                or provenance.get("basis") != "full_text"
                or provenance.get("source_binding") != review["review_identity"]):
            self.metadata["issues"][key] = "projection_unqualified"
            self.discard_record(key)
            return None
        if (record["candidate"] != candidate.model_dump(mode="json")
                or record["triage"] != triage.model_dump(mode="json")):
            self.metadata["issues"][key] = "record_input_mismatch"
            return None
        self.metadata["cards_reused"] += 1
        self.remember_record(key, record)
        return deepcopy(record)

    def _record_from_report(self, key, reference):
        from zotero_summarizer.services.research_feed.reports import get_report

        run_id = reference["run_id"]
        try:
            if run_id not in self.reports:
                self.reports[run_id] = get_report(self.settings, run_id)
            record = self.reports[run_id]["cards"][reference["index"]]
        except APIError as exc:
            self.metadata["issues"][key] = "report_unavailable" if exc.status_code == 404 else "report_invalid"
            return None
        except IndexError:
            self.metadata["issues"][key] = "record_missing"
            return None
        if record["candidate"]["source_id"] != key or canonical_hash(record) != reference["record_sha256"]:
            self.metadata["issues"][key] = "record_checksum_mismatch"
            return None
        return record

    def remember_record(self, key: str, record: dict) -> None:
        if key in self.reviews:
            self.pending[key] = canonical_hash(record)

    def checkpoint(self, run_id: str, records: list[dict]) -> dict:
        """Called after publication, then included in the existing atomic state write."""
        for index, record in enumerate(records):
            key = record["candidate"]["source_id"]
            if key in self.pending and key in self.entries:
                self.entries[key]["card"] = {"run_id": run_id, "index": index,
                                             "review_sha256": self.reviews[key],
                                             "record_sha256": self.pending[key]}
        return {"schema_version": 1, "entries": self.entries}
