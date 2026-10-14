"""Strict supplied-original I/O contracts; no retrieval or semantic grading."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any


def raw_digest(text: str) -> str:
    """Bind exact UTF-8 input, without whitespace or quote normalization."""
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError as exc:
        raise ValueError("invalid_text_encoding") from exc


def _fields(raw: Any, names: set[str], reason: str) -> None:
    if type(raw) is not dict or raw.keys() != names:
        raise ValueError(reason)


def is_utf8_text(value: Any) -> bool:
    """Admit exact strings encodable by the existing strict UTF-8 boundary."""
    if type(value) is not str:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _strings(values: list[Any]) -> bool:
    return all(is_utf8_text(value) and bool(value.strip()) for value in values)


def _span(raw: Any, text: str, reason: str) -> tuple[int, int]:
    _fields(raw, {"start", "end"}, reason)
    start, end = raw["start"], raw["end"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise ValueError(reason)
    return start, end


def validate_source(raw: Any, candidate_id: str, *, max_chars: int, max_passages: int) -> dict[str, Any]:
    _fields(raw, {"candidate_id", "source_id", "source_version_id", "source_sha256", "text",
                  "complete", "truncated", "source_kind", "retrieval_mode", "checked_scope", "passages"},
            "invalid_source")
    if not _strings([raw[key] for key in ("candidate_id", "source_id", "source_version_id", "source_sha256",
                                         "source_kind", "retrieval_mode")]):
        raise ValueError("invalid_source")
    if raw["candidate_id"] != candidate_id or type(raw["text"]) is not str:
        raise ValueError("invalid_source")
    if len(raw["text"]) > max_chars:
        raise ValueError("source_character_budget_exceeded")
    if not raw["text"].strip():
        raise ValueError("source_empty")
    try:
        digest = raw_digest(raw["text"])
    except ValueError as exc:
        raise ValueError("invalid_source") from exc
    if raw["source_sha256"] != digest:
        raise ValueError("invalid_source")
    if type(raw["complete"]) is not bool or type(raw["truncated"]) is not bool:
        raise ValueError("invalid_source")
    if raw["source_kind"] not in ("synthetic_original", "supplied_original"):
        raise ValueError("invalid_source")
    if raw["retrieval_mode"] != "supplied_occurrence_spans":
        raise ValueError("invalid_source")
    scope_start, scope_end = _span(raw["checked_scope"], raw["text"], "invalid_source")
    passages = raw["passages"]
    if type(passages) is not list:
        raise ValueError("invalid_source")
    if len(passages) > max_passages:
        raise ValueError("source_passage_budget_exceeded")
    seen = set()
    for passage in passages:
        _fields(passage, {"passage_id", "start", "end", "quote"}, "invalid_source")
        if not _strings([passage["passage_id"], passage["quote"]]) or passage["passage_id"] in seen:
            raise ValueError("invalid_source")
        start, end = _span({key: passage[key] for key in ("start", "end")}, raw["text"], "invalid_source")
        if not scope_start <= start < end <= scope_end or raw["text"][start:end] != passage["quote"]:
            raise ValueError("invalid_source")
        seen.add(passage["passage_id"])
    return deepcopy(raw)


def validate_context(raw: Any, *, max_chars: int) -> dict[str, Any]:
    _fields(raw, {"availability", "records"}, "invalid_context")
    if type(raw["availability"]) is not str or raw["availability"] not in ("available_nonempty", "available_empty", "unavailable"):
        raise ValueError("invalid_context")
    records = raw["records"]
    if type(records) is not list or bool(records) != (raw["availability"] == "available_nonempty"):
        raise ValueError("invalid_context")
    for row in records:
        _fields(row, {"source_id", "role", "revision", "authorship", "text"}, "invalid_context")
        if not _strings([row["source_id"], row["role"], row["authorship"]]) or row["role"] != "comment":
            raise ValueError("invalid_context")
        if type(row["revision"]) is not int or row["revision"] < 0 or not is_utf8_text(row["text"]):
            raise ValueError("invalid_context")
    if sum(len(row["text"]) for row in records) > max_chars:
        raise ValueError("context_character_budget_exceeded")
    return deepcopy(raw)


def exposes_context(payload: Any, context: dict[str, Any]) -> bool:
    """Literal private-text tripwire, not a meaning judge or general DLP proof."""
    private = [row["text"] for row in context["records"] if row["text"]]
    pending = [payload]
    while pending:
        value = pending.pop()
        if type(value) is str and any(text in value for text in private):
            return True
        if type(value) is dict:
            pending.extend(value.keys())
            pending.extend(value.values())
        elif type(value) is list:
            pending.extend(value)
    return False


def validate_proposal(raw: Any, source: dict[str, Any], *, max_chars: int, max_passages: int) -> dict[str, Any]:
    _fields(raw, {"answer", "evidence"}, "invalid_schema")
    if not _strings([raw["answer"]]) or type(raw["evidence"]) is not list:
        raise ValueError("invalid_schema")
    if len(raw["answer"]) > max_chars or len(raw["evidence"]) > max_passages:
        raise ValueError("output_budget_exceeded")
    passages = {row["passage_id"]: row for row in source["passages"]}
    seen = set()
    for locator in raw["evidence"]:
        _fields(locator, {"passage_id", "start", "end", "quote", "source_id", "source_version_id"}, "invalid_locator")
        texts = [locator[key] for key in ("passage_id", "quote", "source_id", "source_version_id")]
        if any(type(value) is str and not is_utf8_text(value) for value in texts):
            raise ValueError("invalid_schema")
        if not _strings(texts):
            raise ValueError("invalid_locator")
        _span({key: locator[key] for key in ("start", "end")}, source["text"], "invalid_locator")
        passage = passages.get(locator["passage_id"])
        if locator["source_id"] != source["source_id"] or locator["source_version_id"] != source["source_version_id"]:
            raise ValueError("invalid_locator")
        if passage is None or locator["passage_id"] in seen or any(locator[key] != passage[key] for key in passage):
            raise ValueError("invalid_locator")
        seen.add(locator["passage_id"])
    return deepcopy(raw)


def validate_verdict(raw: Any, proposal: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    _fields(raw, {"state", "supported", "evidence_ids", "uncovered"}, "invalid_schema")
    if type(raw["state"]) is not str or raw["state"] not in {"sufficient", "partial", "not_found_in_checked_text", "unknown"}:
        raise ValueError("invalid_schema")
    if type(raw["supported"]) is not bool or raw["supported"] != (raw["state"] in {"sufficient", "partial"}):
        raise ValueError("invalid_schema")
    ids, uncovered = raw["evidence_ids"], raw["uncovered"]
    if type(ids) is not list or type(uncovered) is not list or not _strings(ids + uncovered):
        raise ValueError("invalid_schema")
    allowed = {row["passage_id"] for row in proposal["evidence"]}
    if len(ids) != len(set(ids)) or not set(ids) <= allowed or (raw["supported"] and not ids):
        raise ValueError("invalid_schema")
    if (raw["state"] == "partial" and not uncovered) or (raw["state"] == "sufficient" and uncovered):
        raise ValueError("invalid_schema")
    if sum(len(value) for value in uncovered) > max_chars:
        raise ValueError("output_budget_exceeded")
    return deepcopy(raw)
