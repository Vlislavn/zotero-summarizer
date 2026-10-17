"""Versioned frozen-input wire format and content identity (no live reads)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

RETAINED_PATHS = {
    "triage_db": "inputs/triage.sqlite",
    "golden_csv": "inputs/golden.csv",
    "interaction_log": "inputs/interaction-events.jsonl",
    "classifier_runs_log": "inputs/classifier-runs.jsonl",
}
ALIGNMENT = {
    "cross_store_atomic": False,
    "cutoff_semantics": "logical_filter_not_snapshot_time",
    "golden_csv_history": "unknown",
    "inference_identity": "caller_declared_only",
}
CAVEATS = {
    "triage_db": "SQLite online backup is internally consistent; capture time is not the logical cutoff.",
    "golden_csv": "Opened-file snapshot; historical row timing unknown; not a cross-store transaction.",
    "interaction_log": "Known EOF prefix, checked twice; no lock against external in-place writers.",
    "classifier_runs_log": "Known EOF prefix, checked twice; declared models are not inferred historical identity.",
}


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _require_json(value) -> None:
    if value is None or type(value) in (str, int, float, bool):
        canonical_bytes(value)
    elif type(value) is list:
        for child in value:
            _require_json(child)
    elif type(value) is dict and all(type(key) is str for key in value):
        for child in value.values():
            _require_json(child)
    else:
        raise ValueError("provenance must contain only JSON values and string keys")


def declared_provenance(value: dict) -> dict:
    if type(value) is not dict or not {"code", "config", "environment", "seed"} <= value.keys():
        raise ValueError("declare code, config, environment and seed provenance")
    _require_json(value)
    return json.loads(canonical_bytes(value))


def utc_iso(value: datetime) -> str:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("cutoff and capture times must be aware UTC")
    return value.isoformat().replace("+00:00", "Z")


def timestamp_now() -> str:
    return utc_iso(datetime.now(timezone.utc))


def _timestamp(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO UTC string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if utc_iso(parsed) != value:
        raise ValueError("timestamp must use canonical ISO UTC")
    return parsed


def digest_manifest(manifest: dict) -> str:
    identity = {
        "schema_version": manifest["schema_version"], "cutoff": manifest["cutoff"],
        "provenance": manifest["provenance"],
        "sources": {name: {key: source[key] for key in ("status", "sha256", "byte_size")}
                    for name, source in manifest["sources"].items()},
    }
    return hashlib.sha256(canonical_bytes(identity)).hexdigest()


def source_record(name: str, locator: Path, *, start: str, end: str, size: int = 0,
                  sha256: str | None = None, sqlite: dict | None = None) -> dict:
    present = sha256 is not None
    record = {
        "status": "present" if present else "missing",
        "path": RETAINED_PATHS[name] if present else None,
        "sha256": sha256, "byte_size": size,
        "source_locator": str(locator),
        "capture": {"started_at": start, "finished_at": end,
                    "eof_offset": size if present and name != "triage_db" else None},
        "caveat": CAVEATS[name],
    }
    if name == "triage_db":
        record["sqlite"] = sqlite
    return record


def _is_hash(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _validate_source(name: str, source) -> None:
    keys = {"status", "path", "sha256", "byte_size", "source_locator", "capture", "caveat"}
    if name == "triage_db":
        keys.add("sqlite")
    if type(source) is not dict or source.keys() != keys:
        raise ValueError(f"invalid source shape: {name}")
    if source["status"] not in ("present", "missing"):
        raise ValueError(f"invalid source status: {name}")
    present = source["status"] == "present"
    if type(source["byte_size"]) is not int or source["byte_size"] < 0:
        raise ValueError(f"invalid captured size: {name}")
    if present:
        if source["path"] != RETAINED_PATHS[name] or not _is_hash(source["sha256"]):
            raise ValueError(f"invalid retained path or full hash: {name}")
    elif (source["path"], source["sha256"], source["byte_size"]) != (None, None, 0):
        raise ValueError(f"missing source has retained content: {name}")
    if not isinstance(source["source_locator"], str) or not Path(source["source_locator"]).is_absolute():
        raise ValueError(f"invalid source locator: {name}")
    if source["caveat"] != CAVEATS[name]:
        raise ValueError(f"missing consistency caveat: {name}")
    capture = source["capture"]
    if type(capture) is not dict or capture.keys() != {"started_at", "finished_at", "eof_offset"}:
        raise ValueError(f"invalid capture bounds: {name}")
    if _timestamp(capture["started_at"]) > _timestamp(capture["finished_at"]):
        raise ValueError(f"reversed capture interval: {name}")
    eof = source["byte_size"] if present and name != "triage_db" else None
    if capture["eof_offset"] != eof or (eof is not None and type(capture["eof_offset"]) is not int):
        raise ValueError(f"invalid captured EOF: {name}")
    if name == "triage_db" and (not present or type(source["sqlite"]) is not dict):
        raise ValueError("mandatory triage snapshot is missing")


def validate_manifest(manifest) -> dict:
    keys = {"schema_version", "cutoff", "provenance", "alignment", "sources", "content_digest"}
    if type(manifest) is not dict or manifest.keys() != keys:
        raise ValueError("invalid input manifest shape")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ValueError("unsupported input manifest version")
    _timestamp(manifest["cutoff"])
    declared_provenance(manifest["provenance"])
    if canonical_bytes(manifest["alignment"]) != canonical_bytes(ALIGNMENT):
        raise ValueError("invalid cross-store alignment declaration")
    sources = manifest["sources"]
    if type(sources) is not dict or sources.keys() != RETAINED_PATHS.keys():
        raise ValueError("missing or unknown manifest sources")
    for name, source in sources.items():
        _validate_source(name, source)
    if not _is_hash(manifest["content_digest"]) or digest_manifest(manifest) != manifest["content_digest"]:
        raise ValueError("input content identity mismatch")
    return manifest
