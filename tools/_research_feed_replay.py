"""Offline v2 capture qualification and adapter to the production weekly ranker."""
from __future__ import annotations

from datetime import datetime
import math
from urllib.parse import urlsplit

from zotero_summarizer.models import ResearchCandidate, ResearchFeedTriage
from zotero_summarizer.services.research_feed import ranking
from zotero_summarizer.storage import feeds


_MISSING = object()
_NATIVE_REASONS = {
    feeds.DECISION_SELECTED: "selected",
    feeds.DECISION_USER_REJECTED: "native_veto",
    feeds.DECISION_GATE_REJECTED: "native_veto",
    feeds.DECISION_REJECTED_LOW_SCORE: "native_veto",
    feeds.DECISION_REJECTED_DEDUP_LIBRARY: "duplicate",
    feeds.DECISION_REJECTED_DEDUP_PROCESSED: "duplicate",
    feeds.DECISION_SKIPPED_ERROR: "skipped_error",
    feeds.DECISION_TRIAGED_PENDING: "triage_rule",
    feeds.DECISION_AWAITING_REVIEW: "triage_rule",
    feeds.DECISION_REJECTED_DAILY_CUTOFF: "triage_rule",
    feeds.DECISION_REJECTED_ELBOW: "triage_rule",
    feeds.DECISION_USER_APPROVED: "triage_rule",
    feeds.DECISION_BLACK_SWAN: "triage_rule",
}


def _bad(path):
    raise ValueError(f"malformed frozen replay: {path}") from None


def _object(value, path, required, missing, optional=()):
    if value is _MISSING:
        missing.append(path)
        return {}
    if type(value) is not dict:
        _bad(path)
    if value.keys() - set(required) - set(optional):
        _bad(path)
    missing.extend(f"{path}.{name}" for name in required if name not in value)
    return value


def _text(value, path):
    if type(value) is not str:
        _bad(path)
    return value


def _identifier(value, path):
    if not _text(value, path).strip():
        _bad(path)
    return value


def _choice(value, choices, path):
    if type(value) is not str or value not in choices:
        _bad(path)


def _version(value, path):
    if type(value) is not int or value != 2:
        _bad(path)


def _date(value, path, *, aware=False):
    _text(value, path)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _bad(path)
    if aware and parsed.utcoffset() is None:
        _bad(path)


def declares_v2(payload):
    """A declaration cannot fall back to legacy, even without its envelope."""
    if type(payload) is not dict or type(payload.get("papers")) is not list:
        _bad("papers")
    declared = "frozen_cohort" in payload
    if declared:
        envelope = payload["frozen_cohort"]
        if type(envelope) is not dict:
            _bad("frozen_cohort")
        if "schema_version" in envelope:
            _version(envelope["schema_version"], "frozen_cohort.schema_version")
    for index, row in enumerate(payload["papers"]):
        if type(row) is not dict:
            _bad(f"papers[{index}]")
        machine = row.get("frozen_machine")
        if type(machine) is dict and "schema_version" in machine:
            declared = True
            _version(machine["schema_version"], f"papers[{index}].frozen_machine.schema_version")
    return declared


def _candidate(value, path, missing):
    raw = _object(value, path, ResearchCandidate.model_fields, missing)
    lists = {"authors", "code_urls", "dataset_urls", "model_urls"}
    nullable = {"doi", "venue", "pdf_url", "published_at", "updated_at"}
    for name, item in raw.items():
        field = f"{path}.{name}"
        if item is None and name in nullable:
            continue
        if name in lists:
            if type(item) is not list or any(type(part) is not str for part in item):
                _bad(field)
        elif name in {"published_at", "updated_at"}:
            _date(item, field)
        else:
            _text(item, field)
        if name == "source_id":
            _identifier(item, field)
        if name in {"abstract", "url"} and not item.strip():
            missing.append(field)
        elif name == "url":
            try:
                url = urlsplit(item)
                if url.scheme not in {"http", "https"} or not url.hostname:
                    _bad(field)
            except ValueError:
                _bad(field)
    if not ResearchCandidate.model_fields.keys() <= raw.keys():
        return None
    try:
        return ResearchCandidate.model_validate(raw)
    except ValueError:
        _bad(path)


def _triage(value, path, missing):
    raw = _object(value, path, ResearchFeedTriage.model_fields, missing)
    lists = {"matched_themes", "matched_projects", "contribution_kind"}
    for name, item in raw.items():
        field = f"{path}.{name}"
        if name == "include":
            if type(item) is not bool:
                _bad(field)
        elif name == "score":
            if type(item) is not int or not 1 <= item <= 5:
                _bad(field)
        elif name == "confidence":
            if type(item) not in (int, float) or not 0 <= item <= 1 or not math.isfinite(item):
                _bad(field)
        elif name in lists:
            if type(item) is not list or any(type(part) is not str for part in item):
                _bad(field)
        elif name == "artifact_signal":
            _choice(item, {"present", "absent", "unknown"}, field)
        else:
            _text(item, field)
    if not ResearchFeedTriage.model_fields.keys() <= raw.keys():
        return None
    try:
        return ResearchFeedTriage.model_validate(raw)
    except ValueError:
        _bad(path)


def _admission(value, path, missing):
    raw = _object(value, path, ("status", "reason", "native_decision"), missing)
    reasons = {
        "eligible": {"selected", "triage_rule", "no_prior_history"},
        "excluded": {"native_veto", "duplicate", "triage_rule", "no_prior_history"},
        "failed": {"skipped_error", "invalid_native_decision", "triage_history_invalid", "history_withheld"},
    }
    if "status" in raw:
        _choice(raw["status"], reasons, f"{path}.status")
    if "reason" in raw:
        _choice(raw["reason"], set.union(*reasons.values()), f"{path}.reason")
    native = raw.get("native_decision", _MISSING)
    if native is not _MISSING and native is not None:
        _choice(native, _NATIVE_REASONS, f"{path}.native_decision")
    if "status" in raw and "reason" in raw and raw["reason"] not in reasons[raw["status"]]:
        _bad(path)
    if "reason" in raw and native is not _MISSING:
        reason = raw["reason"]
        if reason in {"no_prior_history", "history_withheld", "invalid_native_decision"}:
            if native is not None:
                _bad(path)
        elif reason != "triage_history_invalid" and _NATIVE_REASONS.get(native) != reason:
            _bad(path)
    return raw


def _provenance(value, path, missing):
    raw = _object(value, path, ("basis", "evidence_basis", "source_history"), missing)
    if "basis" in raw:
        _choice(raw["basis"], {"native_pre_human_b1_output"}, f"{path}.basis")
    if "evidence_basis" in raw:
        _choice(raw["evidence_basis"], {"captured_row", "consumed_field_projection"}, f"{path}.evidence_basis")
    optional = ("row_id", "run_id", "model_version", "created_at", "updated_at")
    required = ("history", "reason", "text_basis", "currentness", "content_version")
    history = _object(raw.get("source_history", _MISSING), f"{path}.source_history", required, missing, optional)
    reasons = {
        "accepted": {"identity_compatible"}, "missing": {"no_prior_history"},
        "withheld": {"history_source_key_mismatch", "history_source_type_unverifiable", "history_title_mismatch",
                     "history_doi_mismatch", "history_abstract_unverifiable", "history_abstract_conflict"},
    }
    for name, item in history.items():
        field = f"{path}.source_history.{name}"
        if name == "history":
            _choice(item, reasons, field)
        elif name == "reason":
            _choice(item, set.union(*reasons.values()), field)
        elif name == "text_basis":
            _choice(item, {"rss", "stored", "not_available"}, field)
        elif name in {"currentness", "content_version"}:
            _choice(item, {"not_attested"}, field)
        elif name == "row_id":
            if type(item) is not int:
                _bad(field)
        elif item is not None:
            _text(item, field)
    if "history" in history:
        status = history["history"]
        if "reason" in history and history["reason"] not in reasons[status]:
            _bad(f"{path}.source_history")
        if status != "accepted" and (set(optional) & history.keys() or history.get("text_basis") == "stored"):
            _bad(f"{path}.source_history")
    return history


def _machine(value, path, missing):
    fields = ("schema_version", "candidate", "admission", "triage", "rank_row", "provenance")
    raw = _object(value, path, fields, missing)
    if "schema_version" in raw:
        _version(raw["schema_version"], f"{path}.schema_version")
    candidate = _candidate(raw.get("candidate", _MISSING), f"{path}.candidate", missing)
    admission = _admission(raw.get("admission", _MISSING), f"{path}.admission", missing)
    history = _provenance(raw.get("provenance", _MISSING), f"{path}.provenance", missing)
    triage_raw = raw.get("triage", _MISSING)
    triage = None if triage_raw is None else _triage(triage_raw, f"{path}.triage", missing)
    status = admission.get("status")
    if triage_raw is not _MISSING and status is not None:
        if (status == "failed") != (triage_raw is None):
            _bad(f"{path}.triage")
        if type(triage_raw) is dict and "include" in triage_raw and triage_raw["include"] != (status == "eligible"):
            _bad(f"{path}.triage.include")
    row = raw.get("rank_row", _MISSING)
    if row is None:
        if status == "eligible":
            _bad(f"{path}.rank_row")
    else:
        row = _object(row, f"{path}.rank_row", ("composite_score", "shap_contribs_json"), missing)
        blob = row.get("shap_contribs_json")
        if blob is not None:
            _text(blob, f"{path}.rank_row.shap_contribs_json")
    if history.get("history") in {"missing", "withheld"} and admission.get("native_decision") is not None:
        _bad(f"{path}.admission.native_decision")
    if "history" in history and "reason" in admission:
        reason = admission["reason"]
        if history["history"] == "withheld" and reason != "history_withheld":
            _bad(f"{path}.admission")
        if history["history"] == "missing" and reason not in {"no_prior_history", "triage_history_invalid"}:
            _bad(f"{path}.admission")
        if history["history"] == "accepted" and reason in {"no_prior_history", "history_withheld"}:
            _bad(f"{path}.admission")
    native = raw.get("candidate", {}).get("source_id")
    return native, candidate, triage, row


def _envelope(value, missing):
    fields = ("schema_version", "rank_contract", "cohort_id", "capture_phase", "captured_at",
              "producer_revision", "source_order_ids")
    raw = _object(value, "frozen_cohort", fields, missing)
    for name, item in raw.items():
        path = f"frozen_cohort.{name}"
        if name == "schema_version":
            _version(item, path)
        elif name == "rank_contract":
            _choice(item, {"weekly_rank_v1"}, path)
        elif name == "capture_phase":
            _choice(item, {"pre_human_b1_output"}, path)
        elif name == "captured_at":
            _date(item, path, aware=True)
        elif name == "source_order_ids":
            if type(item) is not list:
                _bad(path)
            for source_id in item:
                _identifier(source_id, path)
            if len({source_id.strip() for source_id in item}) != len(item):
                _bad(path)
        else:
            _identifier(item, path)
    return raw


def unscorable_result(basis, missing, source_count=None):
    return {
        "inclusion_basis": {"missing_frozen_machine": "unscorable_missing_frozen_machine",
                            "requires_v2_snapshot": "unscorable_requires_v2_snapshot",
                            "incomplete_v2_snapshot": "unscorable_incomplete_v2_snapshot"}[basis],
        "unscorable_ids": list(missing), "unscorable_fields": missing,
        "shortlist_precision_at_10": None, "eligible_must_not_miss_recall_at_10": None,
        "undefined_metrics": {},
        "rank_replay": {"status": "unscorable", "basis": basis, "contract": None,
                        "scope": "conditional_b1_eligible", "source_cohort_size": source_count,
                        "eligible_count": None, "scored_count": None, "unscored_count": None,
                        "ordered_source_ids": None},
    }


def _labels(row, path, missing):
    for name in ("source_id", "human_include", "must_not_miss"):
        if name not in row:
            missing.append(name)
        elif name == "source_id":
            _identifier(row[name], f"{path}.source_id")
        elif type(row[name]) is not bool:
            _bad(f"{path}.{name}")
    return row["source_id"].strip() if "source_id" in row else None


def _grade(ordered, metadata, labels, source_count):
    ids = [candidate.source_id for candidate, _triage in ordered]
    selected = ids[:10]
    must = [key for key in ids if labels[key.strip()]["must_not_miss"]]
    undefined = {}
    if not selected:
        undefined["shortlist_precision_at_10"] = "empty_selected_set"
    if not must:
        undefined["eligible_must_not_miss_recall_at_10"] = "no_eligible_must_not_miss_gold"
    entries = metadata["inputs"].values()
    return {
        "inclusion_basis": "frozen_v2_conditional_b2_rank", "unscorable_ids": [], "unscorable_fields": {},
        "shortlist_precision_at_10": sum(labels[key.strip()]["human_include"] for key in selected) / len(selected)
        if selected else None,
        "eligible_must_not_miss_recall_at_10": sum(key in selected for key in must) / len(must) if must else None,
        "undefined_metrics": undefined,
        "rank_replay": {"status": "scorable", "basis": "pre_human_b1_output", "contract": metadata["contract"],
                        "scope": "conditional_b1_eligible", "source_cohort_size": source_count,
                        "eligible_count": len(ids), "scored_count": sum(e["status"] == "scored" for e in entries),
                        "unscored_count": sum(e["status"] == "unscored" for e in entries), "ordered_source_ids": ids},
    }


def evaluate_replay(payload):
    """Validate all present outer evidence, then rank only a complete whole snapshot."""
    envelope_missing = []
    envelope = _envelope(payload.get("frozen_cohort", _MISSING), envelope_missing)
    order = envelope.get("source_order_ids")
    missing, machines, normalized, labels, assessed, rows = {}, set(), set(), {}, [], {}
    case_ids = set()
    for index, record in enumerate(payload["papers"]):
        path = f"papers[{index}]"
        fields = list(envelope_missing)
        case_id = record.get("id", path)
        if "id" in record:
            _identifier(case_id, f"{path}.id")
            if case_id.strip() in case_ids:
                _bad("papers.id")
            case_ids.add(case_id.strip())
        else:
            fields.append("id")
        label_id = _labels(record, path, fields)
        if label_id is not None:
            if label_id in labels:
                _bad("papers.source_id")
            labels[label_id] = {name: record[name] for name in ("human_include", "must_not_miss") if name in record}
        native, candidate, triage, row = _machine(
            record.get("frozen_machine", _MISSING), "frozen_machine", fields,
        )
        if native is not None:
            if native in machines or native.strip() in normalized:
                _bad("frozen_machine.candidate.source_id")
            machines.add(native)
            normalized.add(native.strip())
            if candidate is not None and triage is not None:
                assessed.append((candidate, triage))
            if row is not None:
                rows[native] = row
        if fields:
            missing[case_id] = list(dict.fromkeys([*missing.get(case_id, []), *fields]))
    if not payload["papers"] and envelope_missing:
        missing["frozen_cohort"] = envelope_missing
    if order is not None:
        if machines - set(order) or labels.keys() - {key.strip() for key in order}:
            _bad("frozen_cohort.source_order_ids")
        for key in order:
            if key not in machines:
                missing.setdefault(key, []).append("frozen_machine")
            if key.strip() not in labels:
                missing.setdefault(key, []).append("source_id")
    if missing:
        count = len(order) if not envelope_missing else None
        return unscorable_result("incomplete_v2_snapshot", missing, count)
    ordered, metadata, failures = ranking.rank_candidates(assessed, rows, order)
    if failures:
        _bad("rank_row.consumed_inputs")
    return _grade(ordered, metadata, labels, len(order))
