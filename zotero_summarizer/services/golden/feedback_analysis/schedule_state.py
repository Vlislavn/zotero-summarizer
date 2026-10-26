"""Private weekly intent, prepublication identity and confirmed-report recovery."""
from __future__ import annotations

import hashlib
import os
import stat
import threading
from datetime import datetime, timedelta
from pathlib import Path

from zotero_summarizer.services._common import atomic_write

from .history import aware_time
from .inputs import _no_symlinks, verify_inputs
from .provenance import canonical_bytes, utc_iso
from .report import PROTOCOL_MAX_BYTES, hash_file, read_protocol, verify_report, write_protocol

INTENT_KEYS = {"schema_version", "intent_id", "window_start", "cutoff", "schedule_rules_sha256"}
CANDIDATE_KEYS = {"schema_version", "intent", "content_key", "attempt_id", "run_id", "run_path",
                  "operation_receipt_path", "output_manifest_sha256", "payload"}


class UnconfirmedPublication(RuntimeError):
    """A prepared attempt is held, never silently republished or confirmed."""


def main_thread_only():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("weekly feedback requires explicit main-thread ownership")


def private_root(settings):
    from .operation import _private_directory, admit_sources
    admit_sources(settings)
    root = _no_symlinks(settings.feedback_schedule_dir)
    if root != settings.feedback_analysis_dir / "schedule":
        raise ValueError("schedule root differs from selected Settings")
    _private_directory(settings.feedback_analysis_dir)
    return _private_directory(root)


def _name(value):
    if type(value) is not str or not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value):
        raise ValueError("invalid schedule artifact name")
    return value


def _sha(value):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("invalid full schedule hash")
    return value


def validate_intent(intent, cutoff):
    if type(intent) is not dict or intent.keys() != INTENT_KEYS or type(intent["schema_version"]) is not int or intent["schema_version"] != 1:
        raise ValueError("unsupported schedule intent")
    end, start = aware_time(intent["cutoff"]), aware_time(intent["window_start"])
    if (end is None or start is None or utc_iso(end) != utc_iso(cutoff) or start != end - timedelta(days=7) or
            intent["cutoff"] != utc_iso(end) or intent["window_start"] != utc_iso(start)):
        raise ValueError("invalid weekly window")
    if end.weekday() != 0 or end.time() != datetime.min.time():
        raise ValueError("weekly cutoff must be Monday 00:00 UTC")
    if intent["intent_id"] != "weekly_" + end.strftime("%Y%m%dT%H%M%SZ"):
        raise ValueError("intent ID differs from weekly cutoff")
    _sha(intent["schedule_rules_sha256"])
    return intent


def validate_receipt(settings, receipt):
    keys = {"schema_version", "intent_id", "window_start", "cutoff", "status", "publication_action",
            "owned_published", "confirmed_report", "content_key", "run_path", "operation_receipt_path",
            "schedule_receipt_path", "late_observations", "recovery", "source_identity"}
    if type(receipt) is not dict or receipt.keys() != keys or type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1:
        raise ValueError("unsupported scheduled receipt")
    intent = {key: receipt[key] for key in ("intent_id", "window_start", "cutoff")}
    intent.update(schema_version=1, schedule_rules_sha256="0" * 64)
    validate_intent(intent, aware_time(receipt["cutoff"]))
    if (receipt["status"] not in {"published", "insufficient_data"} or receipt["confirmed_report"] is not True or
            receipt["publication_action"] not in {"committed", "reused"} or type(receipt["recovery"]) is not bool or
            receipt["owned_published"] is not (receipt["publication_action"] == "committed") or
            (receipt["recovery"] and receipt["owned_published"])):
        raise ValueError("invalid scheduled receipt confirmation")
    _sha(receipt["content_key"])
    _sha(receipt["source_identity"])
    for field, parent, names in (("run_path", "runs", None),
            ("operation_receipt_path", "attempts", {"receipt.json"}),
            ("schedule_receipt_path", "attempts", {"schedule-receipt.json", "schedule-recovery.json"})):
        path = _no_symlinks(receipt[field])
        container = path.parent if names is None else path.parent.parent
        if container != settings.feedback_analysis_dir / parent or (names is not None and path.name not in names):
            raise ValueError("scheduled receipt path differs from Settings")
    late = receipt["late_observations"]
    if (type(late) is not dict or late.keys() != {"at_or_before_previous_cutoff", "basis", "result_path"} or
            type(late["at_or_before_previous_cutoff"]) is not int or not 0 <= late["at_or_before_previous_cutoff"] <= 100000 or
            late["result_path"] != str(Path(receipt["run_path"]) / "result.json") or
            late["basis"] != "retained observations, not new-action identity"):
        raise ValueError("invalid late-observation receipt")
    return receipt


def replace_protocol(path, payload):
    path = _no_symlinks(path)
    raw = canonical_bytes(payload)
    if len(raw) > PROTOCOL_MAX_BYTES:
        raise ValueError("schedule state byte cap exhausted")
    if path.exists() and (not path.is_file() or path.stat().st_mode & 0o077):
        raise ValueError("schedule state must be a private regular file")
    atomic_write(path, lambda temporary: temporary.write_bytes(raw))


def _intent_dir(settings, intent):
    from .operation import _private_directory
    intents = _private_directory(private_root(settings) / "intents")
    return _private_directory(intents / _name(intent["intent_id"]))


def begin_attempt(settings, intent, attempt, request):
    from .operation import _private_directory
    directory = _intent_dir(settings, intent)
    references = _private_directory(directory / "attempts")
    reference = {"schema_version": 1, "attempt_id": request["attempt_id"], "intent": intent}
    write_protocol(references / (request["attempt_id"] + ".json"), reference)
    write_protocol(attempt / "schedule-intent.json", intent)
    replace_protocol(directory / "active.json", reference)


def content_key(payload, intent):
    identity = {key: payload[key] for key in ("input_content_digest", "code", "config", "environment", "seed")}
    identity["window"] = {key: intent[key] for key in ("window_start", "cutoff", "schedule_rules_sha256")}
    return hashlib.sha256(canonical_bytes(identity)).hexdigest()


def source_identity(manifest):
    sources = {name: {key: source[key] for key in ("status", "sha256", "byte_size")}
               for name, source in manifest["sources"].items()}
    return hashlib.sha256(canonical_bytes(sources)).hexdigest()


def _candidate(settings, record):
    if type(record) is not dict or record.keys() != CANDIDATE_KEYS or type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError("unsupported schedule candidate")
    validate_intent(record["intent"], aware_time(record["intent"]["cutoff"]))
    key = _sha(record["content_key"])
    attempt_id, run_id = _name(record["attempt_id"]), _name(record["run_id"])
    if record["run_path"] != str(settings.feedback_analysis_dir / "runs" / run_id):
        raise ValueError("candidate run path differs from Settings")
    receipt = settings.feedback_analysis_dir / "attempts" / attempt_id / "receipt.json"
    if record["operation_receipt_path"] != str(receipt):
        raise ValueError("candidate receipt path differs from Settings")
    if key != content_key(record["payload"], record["intent"]):
        raise ValueError("candidate content identity mismatch")
    _sha(record["output_manifest_sha256"])
    return record


def _verify_published(settings, record, guard):
    record = _candidate(settings, record)
    receipt_path = _no_symlinks(record["operation_receipt_path"])
    if (receipt_path.parent / "failure.json").exists():
        raise UnconfirmedPublication("original operation has a failure receipt")
    try:
        receipt = read_protocol(receipt_path, guard)
    except FileNotFoundError as exc:
        raise UnconfirmedPublication("operation success receipt is absent") from exc
    if (type(receipt) is not dict or receipt.get("published") is not True or
            receipt.get("status") not in {"published", "insufficient_data"} or
            any(receipt.get(key) != value for key, value in record["payload"].items())):
        raise UnconfirmedPublication("operation receipt does not confirm this candidate")
    run = _no_symlinks(record["run_path"])
    if receipt.get("run_path") != str(run) or receipt.get("receipt_path") != str(receipt_path):
        raise ValueError("confirmed receipt locator mismatch")
    child = receipt.get("child", {})
    if child.get("reaped") is not True or child.get("group_absent") is not True:
        raise UnconfirmedPublication("owned child cleanup is unconfirmed")
    identity = {key: record[key] for key in ("attempt_id", "run_id")}
    payload = verify_report(run, record, identity, guard)
    if payload != record["payload"]:
        raise ValueError("confirmed candidate manifest changed")
    manifest = verify_inputs(run / "retained")
    guard.check()
    if manifest["content_digest"] != payload["input_content_digest"]:
        raise ValueError("confirmed retained input identity mismatch")
    if any(manifest["provenance"][key] != payload[key] for key in ("code", "config", "environment", "seed")):
        raise ValueError("confirmed provenance mismatch")
    for path in (run, *[run / name for name in payload["outputs"]], run / "output-manifest.json",
                 run / "retained", run / "retained/inputs"):
        guard.consume(0)
        mode = path.lstat().st_mode
        if mode & 0o277 or (not stat.S_ISREG(mode) and not stat.S_ISDIR(mode)):
            raise ValueError("confirmed report is not private and immutable")
    return receipt, manifest


def _confirmation_paths(attempt, recovery):
    prefix = "schedule-recovery" if recovery else "schedule"
    return attempt / (prefix + "-finalizing"), attempt / (prefix + "-finalized")


def _commit_binding(attempt, record, directory, guard, recovery):
    metadata = _no_symlinks(directory).lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o077:
        raise ValueError("confirmation directory must be private")
    sidecar = attempt / ("schedule-recovery.json" if recovery else "schedule-receipt.json")
    return {"schema_version": 1, "attempt_id": attempt.name, "run_id": record["run_id"],
            "content_key": record["content_key"],
            "operation_receipt_sha256": hash_file(record["operation_receipt_path"], guard, max_bytes=PROTOCOL_MAX_BYTES)["sha256"],
            "schedule_receipt_sha256": hash_file(sidecar, guard, max_bytes=PROTOCOL_MAX_BYTES)["sha256"],
            "directory_identity": {"device": metadata.st_dev, "inode": metadata.st_ino}}


def finalize_scheduled(settings, attempt, record, receipt, guard):
    from .operation import _rename_exclusive
    record = _candidate(settings, record)
    if attempt.parent != settings.feedback_analysis_dir / "attempts":
        raise ValueError("finalization attempt differs from Settings")
    prepared, finalized = _confirmation_paths(attempt, receipt["recovery"])
    _no_symlinks(prepared).mkdir(mode=0o700)
    binding = _commit_binding(attempt, record, prepared, guard, receipt["recovery"])
    write_protocol(prepared / "commit.json", binding)
    os.chmod(prepared / "commit.json", 0o400)
    _no_symlinks(finalized)
    guard.check()
    # The positive native move is the final I/O: no fallible postcommit cleanup.
    _rename_exclusive(prepared, finalized)
    return receipt


def _verify_finalization(attempt, record, guard, *, recovery=False):
    _, finalized = _confirmation_paths(attempt, recovery)
    try:
        binding = read_protocol(finalized / "commit.json", guard)
        expected = _commit_binding(attempt, record, finalized, guard, recovery)
        mode = _no_symlinks(finalized / "commit.json").lstat().st_mode
    except OSError as exc:
        raise UnconfirmedPublication("scheduled finalization is absent or inaccessible") from exc
    except ValueError as exc:
        from .inputs import BudgetExceeded
        if isinstance(exc, BudgetExceeded):
            raise
        raise UnconfirmedPublication("scheduled finalization is invalid") from exc
    if (type(binding) is not dict or binding.keys() != expected.keys() or
            any(type(binding[key]) is not type(value) or binding[key] != value for key, value in expected.items())):
        raise UnconfirmedPublication("scheduled finalization binding differs")
    identity = binding["directory_identity"]
    if (identity.keys() != {"device", "inode"} or any(type(value) is not int or value < 0 for value in identity.values()) or
            mode & 0o277):
        raise UnconfirmedPublication("scheduled owned-directory identity is invalid")


def verify_confirmed(settings, record, guard):
    record = _candidate(settings, record)
    attempt = Path(record["operation_receipt_path"]).parent
    _verify_finalization(attempt, record, guard)
    return _verify_published(settings, record, guard)


def prepare_publication(settings, intent, attempt, payload, child, guard):
    from .operation import _private_directory
    manifest = verify_inputs(attempt / "run-staging/retained")
    guard.check()
    if manifest["content_digest"] != payload["input_content_digest"]:
        raise ValueError("staged input identity differs from report")
    key = content_key(payload, intent)
    keys = _private_directory(private_root(settings) / "keys")
    key_path = keys / (key + ".json")
    if key_path.exists():
        record = _candidate(settings, read_protocol(key_path, guard))
        prior, _ = verify_confirmed(settings, record, guard)
        write_protocol(attempt / "schedule-candidate.json", record)
        return record, prior
    record = {"schema_version": 1, "intent": intent, "content_key": key,
              "attempt_id": payload["attempt_id"], "run_id": payload["run_id"],
              "run_path": str(settings.feedback_analysis_dir / "runs" / payload["run_id"]),
              "operation_receipt_path": str(attempt / "receipt.json"),
              "output_manifest_sha256": child["output_manifest_sha256"], "payload": payload}
    write_protocol(attempt / "schedule-candidate.json", record)
    write_protocol(key_path, record)
    return record, None


def scheduled_receipt(settings, intent, attempt, record, guard, *, committed, recovery=False, previous_cutoff=None):
    receipt, manifest = (_verify_published(settings, record, guard) if committed else verify_confirmed(settings, record, guard))
    late = {"at_or_before_previous_cutoff": 0, "basis": "retained observations, not new-action identity",
            "result_path": str(Path(record["run_path"]) / "result.json")}
    if previous_cutoff is not None:
        previous = aware_time(previous_cutoff)
        result = read_result(Path(record["run_path"]) / "result.json", guard)
        late["at_or_before_previous_cutoff"] = sum(
            aware_time(event["time"]) <= previous for event in result["events"] if aware_time(event["time"]) is not None)
    path = attempt / ("schedule-recovery.json" if recovery else "schedule-receipt.json")
    result = {"schema_version": 1, "intent_id": intent["intent_id"], "window_start": intent["window_start"],
              "cutoff": intent["cutoff"], "status": receipt["status"], "publication_action": "committed" if committed else "reused",
              "owned_published": committed, "confirmed_report": True, "content_key": record["content_key"],
              "run_path": record["run_path"], "operation_receipt_path": record["operation_receipt_path"],
              "schedule_receipt_path": str(path), "late_observations": late, "recovery": recovery,
              "source_identity": source_identity(manifest)}
    validate_receipt(settings, result)
    write_protocol(path, result)
    return finalize_scheduled(settings, attempt, record, result, guard)


def read_result(path, guard):
    from .history import strict_json
    from .inputs import _regular_open
    with _regular_open(_no_symlinks(path)) as stream:
        raw = stream.read(64 * 1024**2 + 1)
    guard.consume(len(raw))
    if len(raw) > 64 * 1024**2:
        raise ValueError("schedule result byte cap exhausted")
    return strict_json(raw)


def _active_reference(path, guard):
    reference = read_protocol(path, guard)
    if (type(reference) is not dict or reference.keys() != {"schema_version", "attempt_id", "intent"} or
            type(reference["schema_version"]) is not int or reference["schema_version"] != 1):
        raise ValueError("invalid active schedule reference")
    _name(reference["attempt_id"])
    intent = reference["intent"]
    validate_intent(intent, aware_time(intent["cutoff"]))
    if path.parent.name != intent["intent_id"]:
        raise ValueError("active intent directory differs from reference")
    return reference


def pending_cutoff(settings, guard):
    directory = _no_symlinks(settings.feedback_schedule_dir / "intents")
    if not directory.exists():
        return None
    oldest = None
    with os.scandir(directory) as entries:
        for count, entry in enumerate(entries, 1):
            guard.consume(0)
            if count > guard.budgets["max_source_files"]:
                raise ValueError("schedule intent inventory cap exhausted")
            active = _no_symlinks(Path(entry.path) / "active.json")
            if active.exists():
                reference = _active_reference(active, guard)
                cutoff = aware_time(reference["intent"]["cutoff"])
                oldest = cutoff if oldest is None else min(oldest, cutoff)
    return oldest


def _failed_before_candidate(settings, attempt, intent, guard):
    path = attempt / "failure.json"
    try:
        failure = read_protocol(path, guard)
    except FileNotFoundError:
        return False
    expected = {"schema_version": 1, "attempt_id": attempt.name, "status": "failure", "published": False,
                "publication_state": "not_published", "attempt_path": str(attempt), "receipt_path": str(path),
                "cutoff": intent["cutoff"]}
    if (type(failure) is not dict or any(type(failure.get(key)) is not type(value) or failure[key] != value
            for key, value in expected.items()) or failure.get("phase") not in
            {"child", "request", "provenance", "freeze", "analysis", "report", "validation"}):
        return False
    child = failure.get("child")
    if type(child) is not dict or child.get("reaped") is not True or child.get("group_absent") is not True:
        return False
    run = settings.feedback_analysis_dir / "runs" / _name(failure.get("run_id"))
    return failure.get("run_path") == str(run) and not _no_symlinks(run).exists()


def recover(settings, intent, guard, *, previous_cutoff=None):
    directory = _intent_dir(settings, intent)
    active = _no_symlinks(directory / "active.json")
    if not active.exists():
        return None
    reference = _active_reference(active, guard)
    if reference["intent"] != intent:
        raise ValueError("active schedule rules differ")
    attempt = settings.feedback_analysis_dir / "attempts" / reference["attempt_id"]
    try:
        record = read_protocol(attempt / "schedule-candidate.json", guard)
    except FileNotFoundError as exc:
        if _failed_before_candidate(settings, attempt, intent, guard):
            return None
        raise UnconfirmedPublication("prepared attempt has no confirmed candidate") from exc
    record = _candidate(settings, record)
    _verify_finalization(attempt, record, guard)
    original, manifest = verify_confirmed(settings, record, guard)
    # A recovery receipt is immutable; repeated cursor failures reuse that same confirmation.
    path = attempt / "schedule-recovery.json"
    if path.exists():
        _verify_finalization(attempt, record, guard, recovery=True)
        result = validate_receipt(settings, read_protocol(path, guard))
        expected = {"schema_version": 1, "intent_id": intent["intent_id"], "window_start": intent["window_start"],
                    "cutoff": intent["cutoff"], "status": original["status"], "publication_action": "reused",
                    "owned_published": False, "confirmed_report": True, "content_key": record["content_key"],
                    "run_path": record["run_path"], "operation_receipt_path": record["operation_receipt_path"],
                    "schedule_receipt_path": str(path), "recovery": True, "source_identity": source_identity(manifest)}
        if (type(result) is not dict or result.keys() != expected.keys() | {"late_observations"} or
                any(result.get(key) != value or type(result.get(key)) is not type(value) for key, value in expected.items())):
            raise ValueError("invalid recovery receipt")
        return result
    return scheduled_receipt(settings, intent, attempt, record, guard, committed=False, recovery=True,
                             previous_cutoff=previous_cutoff)
