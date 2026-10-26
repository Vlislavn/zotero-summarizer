"""Request ownership persistence in Doctor's existing selected-project state file."""
from threading import RLock

from zotero_summarizer.integrations.llm_requests import _summarize_requests
from zotero_summarizer.models.providers import STAGES, resolve_stage
from zotero_summarizer.services._common import read_config, read_json_or_empty, write_json_atomic

OWNERSHIP_REVISION = "request-inventory-v1"
_STATE = RLock()


def _path(settings):
    return settings.data_dir / "setup_doctor.json"


def _fenced(attempt, *, local_only=True):
    return any((row["local"] or not local_only) and ((row["started"] and (not row["terminal"] or row["invalid_events"]))
                                 or (not row["started"] and not row["not_submitted"]))
               for row in attempt["requests"])


def request_attempts(settings):
    with _STATE:
        return read_json_or_empty(_path(settings)).get("request_attempts", {})


def _valid_attempts(attempts):
    if not isinstance(attempts, dict):
        return False
    for run_id, attempt in attempts.items():
        if not isinstance(run_id, str) or not run_id or not isinstance(attempt, dict):
            return False
        rows, summary = attempt.get("requests"), attempt.get("completion")
        if not isinstance(rows, list) or not isinstance(summary, dict):
            return False
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("run_id") != run_id:
                return False
            request_id = row.get("request_id")
            identity, reason = row.get("identity"), row.get("reason_code")
            if not isinstance(identity, str) or not identity or not isinstance(reason, str):
                return False
            if not isinstance(request_id, str) or not request_id or request_id in seen:
                return False
            seen.add(request_id)
            if any(type(row.get(key)) is not bool for key in
                   ("expected", "local", "started", "terminal", "successful", "invalid_events", "not_submitted")):
                return False
            if (not row["expected"] or (row["terminal"] and not row["started"])
                    or (row["started"] and row["not_submitted"])
                    or (row["successful"] and (not row["terminal"] or row["invalid_events"]))):
                return False
        expected = _summarize_requests(rows)
        if set(summary) != set(expected) or any(type(summary[key]) is not type(value) or summary[key] != value
                                               for key, value in expected.items()):
            return False
    return True


def local_request_fence(settings):
    attempts = request_attempts(settings)
    return not _valid_attempts(attempts) or any(_fenced(attempt) for attempt in attempts.values())


def record_requests(settings, run_id, requests, completion):
    """Late evidence replaces only its original attempt, never a successor's record."""
    with _STATE:
        payload = read_json_or_empty(_path(settings))
        previous = payload.get("request_attempts", {})
        if not _valid_attempts(previous):
            raise ValueError("Saved request ownership is invalid; no dispatch is permitted")
        known_starts = {row["request_id"] for row in previous.get(run_id, {}).get("requests", []) if row["started"]}
        starting_local = any(row["local"] and row["started"] and row["request_id"] not in known_starts
                             for row in requests)
        if starting_local and any(key != run_id and _fenced(value) for key, value in previous.items()):
            from zotero_summarizer.integrations.llm_requests import _RequestStartRefused

            raise _RequestStartRefused("An unresolved predecessor prevents local dispatch")
        attempts = {key: value for key, value in previous.items() if key == run_id or _fenced(value, local_only=False)}
        attempts[run_id] = {"requests": requests, "completion": completion}
        payload["request_attempts"] = attempts
        if any(_fenced(value) for value in attempts.values()):
            payload.update(ready=False, status="needs_action", modes={})
        write_json_atomic(_path(settings), payload)


def save_doctor(settings, payload):
    """Ordinary checklist/config/credential publication cannot erase request ownership."""
    with _STATE:
        attempts = read_json_or_empty(_path(settings)).get("request_attempts", {})
        payload["request_attempts"] = attempts
        withhold_local_ready(settings, payload)
        write_json_atomic(_path(settings), payload)


def withhold_local_ready(settings, payload):
    attempts = payload.get("request_attempts", {})
    valid = _valid_attempts(attempts)
    held = valid and any(row["local"] and row["started"] and (not row["terminal"] or row["invalid_events"])
                         for attempt in attempts.values() for row in attempt["requests"])
    fenced = not valid or any(_fenced(attempt) for attempt in attempts.values())
    payload.update(request_hold=bool(held) if valid else None, request_fence=fenced,
                   request_state="observed" if valid else "invalid")
    if not fenced:
        return
    import yaml

    try:
        config = read_config(settings.config_path, settings.calibration_path)
        applies = config.llm_enabled and any(resolve_stage(config.llm_routing, stage).provider.is_local
                                             for stage in STAGES)
    except (OSError, ValueError, yaml.YAMLError):
        applies = True  # An invalid configuration cannot turn unresolved work into Ready.
    if not applies:
        return
    from zotero_summarizer.services.setup.doctor_environment import _row

    modes = dict(payload.get("modes") or {})
    modes.update(local_inference="needs_action", offline_ready="needs_action")
    if modes.get("strict_offline") == "ready":
        modes["strict_offline"] = "needs_action"
    payload.update(ready=False, status="needs_action", modes=modes)
    payload["checks"] = [
        _row(row["id"], "needs_action", "Local request ownership remains unproven",
             "Exact original request evidence is unresolved; configuration changes and unrelated success cannot clear it.",
             action="Review AI settings") if row["id"] in {"llm_inference", "dry_run"} else row
        for row in payload.get("checks", [])
    ]
    for row in payload.get("checks", []):
        if row["id"] in {"llm_inference", "dry_run"}:
            row.update(reason_code="local_request_fence")
            row["recovery"]["action"] = "open_ai_settings"
