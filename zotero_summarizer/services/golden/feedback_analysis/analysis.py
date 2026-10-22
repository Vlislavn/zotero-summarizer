"""One retained-only result boundary; no publication, training or live mutations."""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields
from datetime import timedelta
from pathlib import Path

from zotero_summarizer.domain import READING_PRIORITY_SORT_RANK, feedback_verdict_from_signal

from .history import Families, aware_time, content_group, gap, load_history, strict_json, time_fields
from zotero_summarizer.storage.feed_identity import row_feed_keys
from .inputs import BudgetExceeded, verify_inputs
from .metrics import chronological, periods
from .provenance import canonical_bytes

MAX_RECORDS = 100000
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_CSV_FIELD_CHARS = MAX_DECODED_BYTES
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
DEFAULT_WALL_CAP_SECONDS = 60.0
MAX_WALL_CAP_SECONDS = 600.0
WALL_CAP_ENV = "ZS_FEEDBACK_ANALYSIS_WALL_CAP_SECONDS"
DEFAULT_SPLIT_DAYS = 30
RESULT_SCHEMA_VERSION = 1
EXPLICIT_EVENT_SOURCES = {"user", "zotero"}
RULES = {
    "identity": "retained_current_review_note_family_edges_numeric_conflicts_quarantined",
    "event_identity": "locator_is_observation_identity_content_digest_is_not_action_identity",
    "reconciliation": "journal_authoritative_categories_cross_channel_consistency_unconfirmed_never_summed",
    "time": "aware_recorded_offsets_only_naive_origin_unproven_excluded",
    "labels": "explicit_user_or_zotero_plus_offline_sync_and_sync_conflict_resolution_transitions_only_"
              "machine_add_auto_quality_and_outcomes_not_preferences",
    "predictions": "saved_at_decision_only_no_current_result_or_training_model_substitution",
    "csv": "current_snapshot_only_unknown_label_comment_feature_timing",
    "confidence": "Wilson95_unique_observed_papers_selection_biased_not_sufficiency",
}


@dataclass(frozen=True)
class AnalysisBudgets:
    max_records: int = MAX_RECORDS
    max_source_bytes: int = MAX_SOURCE_BYTES
    max_line_bytes: int = MAX_LINE_BYTES
    max_decoded_bytes: int = MAX_DECODED_BYTES
    max_output_bytes: int = MAX_OUTPUT_BYTES
    wall_cap_seconds: float = DEFAULT_WALL_CAP_SECONDS
    split_at: str | None = None
    max_csv_field_chars: int = MAX_CSV_FIELD_CHARS

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "split_at":
                if value is not None and aware_time(value) is None:
                    raise ValueError("split_at must be an aware ISO timestamp")
            elif field.name == "wall_cap_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= MAX_WALL_CAP_SECONDS:
                    raise ValueError("invalid analysis safety wall cap")
            elif type(value) is not int or not 0 < value <= field.default:
                raise ValueError(f"{field.name} must be a positive integer <= {field.default}")


def _budgets(value):
    wall = float(os.environ.get(WALL_CAP_ENV, DEFAULT_WALL_CAP_SECONDS))
    default = AnalysisBudgets(wall_cap_seconds=wall)
    if value is None:
        return default
    if isinstance(value, AnalysisBudgets):
        return value
    if type(value) is not dict or value.keys() - {f.name for f in fields(AnalysisBudgets)}:
        raise ValueError("budgets must be AnalysisBudgets or a known-field dict")
    return AnalysisBudgets(**({"wall_cap_seconds": default.wall_cap_seconds} | value))


class _Guard:
    def __init__(self, budgets):
        self.budgets = budgets
        self.deadline = time.monotonic() + budgets.wall_cap_seconds
        self.record_count = 0
        self.decoded_bytes = 0

    def expired(self):
        return time.monotonic() >= self.deadline

    def check(self):
        if self.expired():
            raise BudgetExceeded("analysis safety wall cap exhausted")

    def source(self, size):
        self.check()
        if size > self.budgets.max_source_bytes:
            raise BudgetExceeded("analysis source byte budget exhausted")

    def line(self, raw):
        self.check()
        if len(raw) > self.budgets.max_line_bytes:
            raise BudgetExceeded("analysis line byte budget exhausted")

    def csv_fields(self, row):
        self.check()
        if any(len(value) > self.budgets.max_csv_field_chars for value in row):
            raise BudgetExceeded("analysis CSV field character budget exhausted")

    def record(self, value):
        self.record_count += 1
        if self.record_count > self.budgets.max_records:
            raise BudgetExceeded("analysis record budget exhausted")
        self.decoded(value)

    def decoded(self, value):
        self.check()
        self.decoded_bytes += len(value) if isinstance(value, bytes) else len(canonical_bytes(value))
        if self.decoded_bytes > self.budgets.max_decoded_bytes:
            raise BudgetExceeded("analysis decoded byte budget exhausted")

    def output(self, value):
        size = 0
        digest = hashlib.sha256()
        encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        for chunk in encoder.iterencode(value):
            self.check()
            raw = chunk.encode("utf-8")
            size += len(raw)
            if size > self.budgets.max_output_bytes:
                raise BudgetExceeded("analysis output byte budget exhausted")
            digest.update(raw)
        return digest.hexdigest()


def _observation_id(locator):
    suffix = f'{locator["table"]}:{locator["row"]}' if "table" in locator else f'line:{locator["line"]}'
    return locator["path"] + "#" + suffix


def _priority(value):
    return value if isinstance(value, str) and value in READING_PRIORITY_SORT_RANK else None


def _base(record, family, cutoff):
    data, channel = record["data"], record["channel"]
    key = data.get("item_key", data.get("item_id", ""))
    if channel == "processed_feed_items":
        key = row_feed_keys(data)[0]
    paper, identity = family.resolve(key, data.get("stable_id"))
    raw_time = data.get("ts") if channel in {"interaction_log", "classifier_runs_log"} else data.get(
        "changed_at", data.get("created_at", data.get("updated_at")))
    when, basis = time_fields(raw_time)
    eligibility = "unknown_time" if when is None else "after_cutoff" if aware_time(when) > cutoff else "eligible"
    if identity in {"ambiguous", "conflicting_stable_id"}:
        eligibility = "ambiguous_identity"
    return {"observation_id": _observation_id(record["locator"]), "locator": record["locator"],
            "content_group": content_group(data), "paper_key": paper, "item_key": key,
            "identity_status": identity, "channel": channel, "kind": "snapshot", "time": when,
            "time_basis": basis, "recorded_time": raw_time, "eligibility": eligibility,
            "previous": None, "value": None, "source": data.get("source"), "surface": data.get("surface"),
            "prediction": None, "comment": data.get("comment", ""), "revision": None,
            "reconciliation": "independent_observation", "event_gate_identity": data.get("gate_sha"),
            "inference_model_identity": None}


def _interaction(event, data):
    kind = data["event"]
    explicit = data.get("source") in EXPLICIT_EVENT_SOURCES
    if kind == "label_transition":
        explicit = explicit or data.get("source") in {"offline_sync", "sync_conflict_resolution"}
        event.update(kind="label_transition" if explicit else "nonpreference", value=data["new_user_priority"],
                     previous=data["previous_user_priority"] if data["history_known"] else None,
                     prediction=_priority(data.get("model_priority")))
        if event["value"] is not None and _priority(event["value"]) is None:
            event["kind"] = "nonpreference"
    else:
        human = data["human"]
        event.update(value=human.get("value"), prediction=_priority(data["model"].get("priority")))
        if kind == "outcome_resolved":
            event["kind"] = "outcome"
        elif data.get("source") == "machine_add":
            event["kind"] = "provisional"
        elif explicit and human.get("kind") == "feedback" and human.get("value") in {"approve", "reject"}:
            event["kind"] = "feedback"
        else:
            event["kind"] = "priority_observation" if explicit and _priority(human.get("value")) else "nonpreference"
    return event


def _journal(event, data, prior, state):
    if data["field"] not in {"verdict", "review_note"}:
        event.update(kind="unknown_field", eligibility="unsupported_record")
        gap(state, "unknown_revision_field", event["locator"])
        return event
    event.update(channel="journal", revision=data["revision"], value=data["value"],
                 kind="review_note" if data["field"] == "review_note" else "label_state",
                 reconciliation="journal_only_context_unavailable")
    key = (event["paper_key"], data["field"])
    previous = prior.get(key)
    if previous is not None and previous["eligibility"] == "eligible" and event["eligibility"] == "eligible":
        if aware_time(previous["time"]) <= aware_time(event["time"]):
            event["previous"] = previous["value"] if previous["source"] == "user" else None
        else:
            gap(state, "revision_time_order_conflict", event["locator"])
            event["eligibility"] = "time_order_conflict"
    if data["field"] == "verdict" and (data.get("source") != "user" or (
            data["value"] is not None and _priority(data["value"]) is None)):
        event["kind"] = "nonpreference"
    if data["field"] == "review_note":
        event["comment"] = data["value"]
    prior[key] = event
    return event


def _snapshot(event, data, state):
    channel = event["channel"]
    if channel == "user_feedback":
        event.update(channel="current_feedback", kind="feedback", value=feedback_verdict_from_signal(data["signal"]),
                     prediction=_priority(data["original_priority"]))
        if event["value"] is None:
            event["kind"] = "nonpreference"
    elif channel == "label_verdicts":
        event.update(value=data["user_priority"], kind="current_label")
        event["eligibility"] = "current_snapshot_only"
    elif channel == "review_notes":
        event.update(kind="current_note", comment=data["note"], eligibility="current_snapshot_only")
    elif channel == "golden_csv":
        event.update(kind="csv_snapshot", value=data.get("gold_priority_final"),
                     comment=data.get("gold_notes", data.get("notes", "")), source=data.get("gold_signal_tier"),
                     eligibility="current_snapshot_only")
    elif channel == "triage_results":
        event.update(kind="current_result", eligibility="current_snapshot_only")
        try:
            payload = strict_json(data["response_json"])
            tags = payload.get("tags", []) if type(payload) is dict else None
            if type(tags) is not list or any(not isinstance(tag, str) for tag in tags):
                raise ValueError("invalid recorded tags")
            event["recorded_categories"] = tags
        except (ValueError, UnicodeDecodeError):
            state["sources"]["triage_db"]["malformed"] += 1
            gap(state, "malformed_saved_result_payload", event["locator"])
    elif channel == "processed_feed_items":
        event.update(kind="feed_snapshot", value=data.get("final_outcome"), source=data.get("source_type"),
                     feed_name=data.get("feed_name"), eligibility="current_snapshot_only")
    elif channel == "classifier_runs_log":
        event.update(kind="training_run", value=data["run_id"], paper_key="", eligibility="not_historical_inference")
    return event


def _overlap_group(event):
    if event["kind"] not in {"label_state", "label_transition", "priority_observation"}:
        return None
    if event["time"] is None:
        return None
    return content_group([event["paper_key"], event["value"], event["comment"],
                          aware_time(event["time"]).replace(microsecond=0).isoformat()])


def _reconcile(events, state):
    groups = defaultdict(Counter)
    for event in events:
        group = _overlap_group(event)
        if group:
            groups[group][event["channel"]] += 1
    for event in events:
        group = _overlap_group(event)
        if group and len(groups[group]) > 1:
            event["reconciliation"] = "consistent_unconfirmed"
            event["overlap_group"] = group
        elif event["channel"] == "interaction_log" and event["kind"] in {"label_transition", "priority_observation"}:
            event["reconciliation"] = "unmatched_unconfirmed"
    if any(len(counts) > 1 for counts in groups.values()):
        gap(state, "unconfirmed_channel_overlap")
    gap(state, "interaction_schema1_has_no_durable_event_identity")


def _item_exclusions(events, family, state, cutoff):
    earliest = {}
    unknown = set()
    for key, values in family.item_times.items():
        paper = family.mapping.get(key, key)
        for value in values:
            parsed = aware_time(value)
            if parsed is None:
                unknown.add(paper)
            else:
                earliest[paper] = min(earliest.get(paper, parsed), parsed)
    for event in events:
        paper = event["paper_key"]
        when = earliest.get(paper)
        if (when is None and paper not in unknown) or event["eligibility"] != "eligible":
            continue
        if when is None or when > aware_time(event["time"]):
            reason = "future_item" if when is not None and when > cutoff and paper not in unknown else "item_time_unproven"
            event["eligibility"] = reason
            gap(state, reason, event["locator"])


def _events(state, family, cutoff, guard):
    events, journals, prior = [], [], {}
    ignored = {"feed_key_aliases", "feed_key_alias_ambiguities"}
    for record in state["records"]:
        guard.check()
        if record["channel"] in ignored:
            continue
        event = _base(record, family, cutoff)
        if record["channel"] == "interaction_log":
            _interaction(event, record["data"])
        elif record["channel"] == "sync_changes":
            journals.append((event, record["data"]))
        else:
            _snapshot(event, record["data"], state)
        if event["eligibility"] == "ambiguous_identity":
            gap(state, "ambiguous_identity", event["locator"])
        elif event["time_basis"] == "naive_origin_unproven":
            gap(state, "unproven_timestamp_origin", event["locator"])
        events.append(event)
    _item_exclusions(events, family, state, cutoff)
    for event, data in journals:
        _journal(event, data, prior, state)
    _reconcile(events, state)
    return events


def _papers(events, family):
    grouped = defaultdict(lambda: {"aliases": set(), "locators": [], "snapshot_observations": [],
                                   "latest_journal": {}, "identity_status": "resolved"})
    for key, canonical in family.mapping.items():
        grouped[canonical]["aliases"].add(key)
        grouped[canonical]["locators"].extend(family.locators.get(key, ()))
    for key in family.ambiguous:
        grouped[key]["aliases"].add(key)
        grouped[key]["locators"].extend(family.locators.get(key, ()))
        grouped[key]["identity_status"] = "ambiguous"
    for event in events:
        key = event["paper_key"]
        if not key:
            continue
        paper = grouped[key]
        paper["aliases"].add(event["item_key"])
        if event["identity_status"] in {"ambiguous", "conflicting_stable_id", "unlinked"} and len(paper["aliases"]) == 1:
            paper["identity_status"] = event["identity_status"]
        if event["eligibility"] == "current_snapshot_only":
            paper["snapshot_observations"].append(event["observation_id"])
        if event["channel"] == "journal":
            field = "review_note" if event["kind"] == "review_note" else "verdict"
            paper["latest_journal"][field] = event["observation_id"]
    result = []
    for key, paper in sorted(grouped.items()):
        locators = {canonical_bytes(v): v for v in paper["locators"]}
        result.append({"canonical_key": key, "aliases": sorted(paper["aliases"]),
                       "identity_status": paper["identity_status"], "locators": list(locators.values()),
                       "current_state_only": paper["snapshot_observations"], "alias_history": "unknown",
                       "retained_latest_journal": paper["latest_journal"],
                       "identity_candidates": sorted(family.candidates.get(key, ())) if key in family.ambiguous else []})
    return result


def analyze_inputs(destination: Path, *, budgets=None) -> dict:
    """Verify a version-1 bundle and return local bounded descriptive evidence."""
    configured = _budgets(budgets)
    guard = _Guard(configured)
    destination = Path(destination).absolute()
    manifest = verify_inputs(destination)
    guard.check()
    cutoff = aware_time(manifest["cutoff"])
    split_at = aware_time(configured.split_at) if configured.split_at else cutoff - timedelta(days=DEFAULT_SPLIT_DAYS)
    if split_at > cutoff:
        raise ValueError("split_at must not exceed cutoff")
    state = load_history(destination, manifest, guard)
    family = Families(state["records"], guard)
    events = _events(state, family, cutoff, guard)
    papers = _papers(events, family)
    if state["sources"]["interaction_log"]["status"] == "missing":
        gap(state, "missing_interaction_history")
    if state["tables"]["sync_changes"]["status"] == "unavailable":
        gap(state, "unrecoverable_trajectory")
    gap(state, "current_alias_mapping_not_historical_identity_availability")
    gap(state, "current_snapshots_not_complete_trajectory")
    guard.check()
    result = {"schema_version": RESULT_SCHEMA_VERSION, "input_content_digest": manifest["content_digest"],
              "cutoff": manifest["cutoff"], "provenance": manifest["provenance"],
              "revision_bounds": manifest["sources"]["triage_db"]["sqlite"]["sync_changes"],
              "config": asdict(configured) | {"split_at": split_at.isoformat().replace("+00:00", "Z"),
                                             "split_default": "cutoff_minus_30days",
                                             "csv_parser_ceiling_chars": configured.max_decoded_bytes,
                                             "csv_effective_field_chars": min(configured.max_csv_field_chars,
                                                                              configured.max_decoded_bytes)},
              "rules": RULES.copy(), "coverage": {"sources": state["sources"], "tables": state["tables"],
                  "unique_current_paper_groups": len(papers), "raw_observations": len(events),
                  "content_multiplicity": dict(Counter(e["content_group"] for e in events)),
                  "exclusions": dict(Counter(e["eligibility"] for e in events if e["eligibility"] != "eligible")),
                  "date_coverage": _date_coverage(events), "snapshot_profiles": _snapshot_profiles(events)},
              "papers": papers, "events": events, "periods": periods(events, cutoff, state["tables"], state["sources"], guard),
              "chronological": chronological(events, papers, cutoff, split_at),
              "ranking": {"status": "unscorable", "precision_at_10": None, "ndcg_at_10": None,
                          "reasons": ["immutable_exposed_ordered_slates_unavailable", "fully_timed_slate_judgments_unavailable"]},
              "gaps": state["gaps"]}
    result["result_digest"] = guard.output(result)
    guard.output(result)
    if verify_inputs(destination)["content_digest"] != manifest["content_digest"]:
        raise ValueError("retained bundle changed during analysis")
    guard.check()
    return result


def _date_coverage(events):
    values = [e["time"] for e in events if e["time"] is not None and e["eligibility"] == "eligible"]
    return {"min": min(values, key=aware_time) if values else None, "max": max(values, key=aware_time) if values else None,
            "unknown": sum(e["time"] is None for e in events)}


def _snapshot_profiles(events):
    categories, outcomes, feeds, labels = set(), set(), set(), set()
    for event in events:
        if event["identity_status"] in {"ambiguous", "conflicting_stable_id"}:
            continue
        for category in event.get("recorded_categories", ()):
            categories.add((event["paper_key"], category))
        if event["kind"] == "feed_snapshot":
            if event["value"] is not None:
                outcomes.add((event["paper_key"], event["value"]))
            if event.get("feed_name"):
                feeds.add((event["paper_key"], event["feed_name"]))
        elif event["kind"] in {"current_label", "csv_snapshot"} and event["value"] is not None:
            labels.add((event["paper_key"], event["value"]))
    return {"basis": "retained_current_snapshot_only_not_historical_changes_or_features",
            "recorded_categories": dict(Counter(category for _, category in categories)),
            "feed_outcomes": dict(Counter(value for _, value in outcomes)),
            "feed_sources": dict(Counter(value for _, value in feeds)),
            "labels": dict(Counter(value for _, value in labels)), "effective_labels": "not_reconstructed"}
