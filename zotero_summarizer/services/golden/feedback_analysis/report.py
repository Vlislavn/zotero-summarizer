"""Private deterministic presentation and bounded publication inventory."""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from zotero_summarizer.services._common import atomic_write

from .provenance import strict_json
from .inputs import BudgetExceeded, _no_symlinks, _regular_open
from .provenance import RETAINED_PATHS, canonical_bytes

__all__ = [
    "hash_file", "inventory", "make_immutable", "markdown", "read_protocol",
    "stage_report", "verify_report", "write_private", "write_protocol",
]

PROTOCOL_MAX_BYTES = 1024 * 1024
OUTPUT_NAMES = {"result.json", "result.md", "retained/input-manifest.json"}
OPTIONAL_NAMES = {"retained/" + p for p in RETAINED_PATHS.values()}


def hash_file(path, guard, *, max_bytes=None):
    path = _no_symlinks(path)
    digest, size = hashlib.sha256(), 0
    with _regular_open(path) as stream:
        before = os.fstat(stream.fileno())
        if max_bytes is not None and before.st_size > max_bytes:
            raise BudgetExceeded("operation artifact byte cap exhausted")
        while chunk := stream.read(65536):
            guard.consume(len(chunk))
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise BudgetExceeded("operation artifact byte cap exhausted")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != before.st_size:
        raise ValueError("publication artifact changed during hashing")
    return {"sha256": digest.hexdigest(), "byte_size": size}


def read_protocol(path, guard):
    with _regular_open(_no_symlinks(path)) as stream:
        raw = stream.read(PROTOCOL_MAX_BYTES + 1)
    guard.consume(len(raw))
    if len(raw) > PROTOCOL_MAX_BYTES:
        raise BudgetExceeded("operation protocol byte budget exhausted")
    return strict_json(raw)


def write_private(path, raw):
    if path.exists() or path.is_symlink():
        raise FileExistsError("operation artifact already exists")
    atomic_write(path, lambda temporary: temporary.write_bytes(raw))


def write_protocol(path, payload):
    raw = canonical_bytes(payload)
    if len(raw) > PROTOCOL_MAX_BYTES:
        raise BudgetExceeded("operation protocol byte budget exhausted")
    write_private(path, raw)


def _shown(value):
    text = str(value).replace("\n", " ").replace("\r", " ")
    marker = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    return f"{marker} {text} {marker}"


def markdown(result, status):
    lines = ["# Feedback evidence", "", f"Status: **{status}** · cutoff `{result['cutoff']}`", "",
             "Descriptive observed support, not proof of usefulness or unbiased improvement.",
             "Historical inference identity is unknown; chronological eligibility is not a scored evaluation.",
             "Ranking remains unscorable without immutable exposure and timed judgments.", "", "## Coverage and unknowns", "",
             f"- Current canonical families: {result['coverage']['unique_current_paper_groups']}",
             f"- Eligible date coverage: {_shown(result['coverage']['date_coverage'])}",
             f"- Exclusions: {_shown(result['coverage']['exclusions'])}",
             "- Journal, log and current feedback are separate overlapping channels, never summed as decisions.", ""]
    lines.extend(f"- {_shown(g['reason'])}" + (f" · {_shown(g['locator'])}" if "locator" in g else "")
                 for g in result["gaps"])
    for window, channels in result["periods"].items():
        lines.extend(["", f"## {window}", ""])
        for name in ("journal", "interaction_log", "current_feedback"):
            channel = channels[name]
            lines.extend([f"### {name} · {channel['status']}", "",
                          f"Unique observed papers: {channel['unique_papers']}. Selection-biased cohort; no sufficiency threshold."])
            for label, support in channel["class_support"].items():
                ci = support["observed_prevalence_ci"]
                lines.append(f"- {label}: {support['unique_papers']} · Wilson 95% [{ci['low']}, {ci['high']}] "
                             f"(denominator {ci['n_unique_papers']} unique observed papers)")
            lines.append(f"- Recorded transitions: {_shown(channel['transitions'])}")
            lines.append(f"- Saved-prediction agreement: {_shown(channel['agreement'])}")
            lines.append(f"- Ordinal error mean/median by surface: {_shown(channel['ordinal_error'])}")
            lines.extend(f"- Disagreement observation: {_shown(locator)}" for locator in channel["disagreements"])
    lines.extend(["", "## Local evidence", "",
                  "`result.json` retains comments and observation IDs with exact local SQL/CSV/JSONL locators, relative to `retained/`.",
                  "`retained/input-manifest.json` is the original input manifest; replay uses only `retained/`.",
                  f"Input identity: `{result['input_content_digest']}`", f"Result identity: `{result['result_digest']}`", ""])
    return "\n".join(lines)


def stage_report(staging, result, identity, guard):
    if result["schema_version"] != 1:
        raise ValueError("unsupported analytic result version")
    observed = any(result["periods"]["all_time"][channel]["unique_papers"] for channel in (
        "journal", "interaction_log", "current_feedback"))
    status = "published" if observed else "insufficient_data"
    raw_json = canonical_bytes(result)
    raw_md = markdown(result, status).encode("utf-8")
    cap = result["config"]["max_output_bytes"]
    if len(raw_json) > cap or len(raw_md) > cap:
        raise BudgetExceeded("operation report output byte budget exhausted")
    guard.consume(len(raw_json) + len(raw_md))
    write_private(staging / "result.json", raw_json)
    write_private(staging / "result.md", raw_md)
    files = inventory(staging, guard)
    outputs = {name: hash_file(staging / name, guard) for name in files}
    payload = {"schema_version": 1, **identity, "status": status, "outputs": outputs,
               "input_content_digest": result["input_content_digest"], "result_digest": result["result_digest"],
               "cutoff": result["cutoff"], "code": result["provenance"]["code"],
               "config": result["provenance"]["config"], "environment": result["provenance"]["environment"],
               "seed": result["provenance"]["seed"], "analysis_budgets": result["config"],
               "gap_reasons": sorted({g["reason"] for g in result["gaps"]})}
    # The frozen config contains two derived parser fields and split_default, not budget inputs.
    payload["analysis_budgets"] = {k: v for k, v in result["config"].items()
                                   if k not in {"split_default", "csv_parser_ceiling_chars", "csv_effective_field_chars"}}
    write_protocol(staging / "output-manifest.json", payload)
    return payload


def inventory(staging, guard, *, published=False):
    files, directories = set(), {"retained", "retained/inputs"}
    for path in staging.rglob("*"):
        guard.consume(0)
        _no_symlinks(path)
        name = path.relative_to(staging).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            files.add(name)
        elif not stat.S_ISDIR(mode) or name not in directories:
            raise ValueError("unexpected publication artifact")
        if len(files) > len(OUTPUT_NAMES | OPTIONAL_NAMES) + 1:
            raise ValueError("unexpected publication inventory")
    expected = OUTPUT_NAMES | {"retained/inputs/triage.sqlite"}
    if published:
        expected.add("output-manifest.json")
    if not expected <= files or files - expected - OPTIONAL_NAMES:
        raise ValueError("incomplete or unexpected publication inventory")
    return sorted(files)


def verify_report(staging, child, identity, guard):
    files = set(inventory(staging, guard, published=True))
    payload = read_protocol(staging / "output-manifest.json", guard)
    keys = {"schema_version", "attempt_id", "run_id", "status", "outputs", "input_content_digest", "result_digest",
            "cutoff", "code", "config", "environment", "seed", "analysis_budgets", "gap_reasons"}
    if type(payload) is not dict or payload.keys() != keys or type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported operation output protocol")
    if any(payload[k] != v for k, v in identity.items()) or payload["status"] not in {"published", "insufficient_data"}:
        raise ValueError("operation identity/status mismatch")
    if type(payload["outputs"]) is not dict or set(payload["outputs"]) != files - {"output-manifest.json"}:
        raise ValueError("operation output inventory mismatch")
    for name, recorded in payload["outputs"].items():
        if hash_file(staging / name, guard) != recorded:
            raise ValueError("operation output hash mismatch")
    actual = hash_file(staging / "output-manifest.json", guard)
    if actual["sha256"] != child["output_manifest_sha256"]:
        raise ValueError("child/output protocol mismatch")
    return payload


def make_immutable(staging, guard, *, include_root=True):
    for path in staging.rglob("*"):
        guard.consume(0)
        os.chmod(_no_symlinks(path), 0o500 if path.is_dir() else 0o400)
    if include_root:
        os.chmod(staging, 0o500)


def _private_directory(path):
    path = _no_symlinks(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise ValueError("operation directory must be private")
    return path
