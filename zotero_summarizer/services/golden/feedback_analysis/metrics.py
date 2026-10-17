"""Selection-biased, unique-paper descriptive reductions, with explicit nulls."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean, median

from zotero_summarizer.domain import READING_PRIORITY_SORT_RANK, is_positive_priority
from zotero_summarizer.services.results import compute_calibration_period

from .history import aware_time

WINDOWS = {"last_7d": 7, "last_30d": 30, "all_time": None}
CONFIDENCE_LEVEL = 0.95
LABEL_CLASSES = tuple(READING_PRIORITY_SORT_RANK)
FEEDBACK_CLASSES = ("approve", "reject")


def confidence(successes, n):
    if not n:
        return {"low": None, "high": None, "n_unique_papers": 0, "level": CONFIDENCE_LEVEL, "method": "Wilson"}
    from scipy.stats import binomtest

    ci = binomtest(successes, n).proportion_ci(CONFIDENCE_LEVEL, method="wilson")
    return {"low": float(ci.low), "high": float(ci.high), "n_unique_papers": n,
            "level": CONFIDENCE_LEVEL, "method": "Wilson"}


def within(event, cutoff, days):
    if event["eligibility"] != "eligible":
        return False
    when = aware_time(event["time"])
    return when <= cutoff and (days is None or when >= cutoff - timedelta(days=days))


def latest_unique(events):
    latest = {}
    conflicts = set()
    for event in events:
        key = event["paper_key"]
        order = (aware_time(event["time"]), event.get("revision") or 0)
        if key not in latest or order > latest[key][0]:
            latest[key] = (order, event)
            conflicts.discard(key)
        elif order == latest[key][0] and (event["value"], event["prediction"]) != (
                latest[key][1]["value"], latest[key][1]["prediction"]):
            conflicts.add(key)
    return [pair[1] for key, pair in latest.items() if key not in conflicts], sorted(conflicts)


def support(rows, classes):
    counts = Counter(e["value"] for e in rows if e["value"] in classes)
    n = sum(counts.values())
    return {category: {"unique_papers": counts[category], "observed_prevalence_ci": confidence(counts[category], n)}
            for category in classes}


def agreement(rows):
    binary = []
    for event in rows:
        value = event["value"]
        if value not in FEEDBACK_CLASSES and value not in LABEL_CLASSES:
            continue
        approved = value == "approve" if value in FEEDBACK_CLASSES else is_positive_priority(value)
        binary.append({"signal": "explicit_approve" if approved else "explicit_reject",
                       "original_priority": event["prediction"]})
    result = compute_calibration_period(binary).model_dump()
    return result, confidence(result["agreement_count"], result["with_prediction_count"])


def ordinal_errors(rows):
    errors = defaultdict(list)
    for event in rows:
        if event["value"] in LABEL_CLASSES and event["prediction"] in LABEL_CLASSES:
            error = abs(READING_PRIORITY_SORT_RANK[event["value"]] - READING_PRIORITY_SORT_RANK[event["prediction"]])
            errors[event["surface"] or "unknown"].append(error)
    values = [v for group in errors.values() for v in group]

    def summary(values):
        return {"mean": mean(values) if values else None, "median": median(values) if values else None,
                "n_unique_papers": len(values)}

    return summary(values) | {"by_surface": {key: summary(v) for key, v in sorted(errors.items())},
                              "unit": "absolute_priority_rank_difference"}


def _disagrees(event):
    if event["prediction"] not in LABEL_CLASSES:
        return False
    if event["value"] in LABEL_CLASSES:
        return event["value"] != event["prediction"]
    if event["value"] in FEEDBACK_CLASSES:
        return (event["value"] == "approve") != is_positive_priority(event["prediction"])
    return False


def channel_metrics(events, status, channel):
    primary = [e for e in events if e["kind"] in {"label_state", "label_transition", "feedback"}]
    labels, label_conflicts = latest_unique([e for e in primary if e["kind"] != "feedback"])
    feedback, feedback_conflicts = latest_unique([e for e in primary if e["kind"] == "feedback"])
    label_papers = {e["paper_key"] for e in labels} | set(label_conflicts)
    latest = labels + [e for e in feedback if e["paper_key"] not in label_papers]
    conflicts = sorted(set(label_conflicts + feedback_conflicts))
    classes = FEEDBACK_CLASSES if channel == "current_feedback" else LABEL_CLASSES
    # Mixed binary/ordinal JSONL observations are reported separately by recorded class.
    if channel == "interaction_log":
        classes += FEEDBACK_CLASSES
    matched = [e for e in latest if e["value"] in classes]
    calibration, ci = agreement(matched)
    transitions = defaultdict(Counter)
    changes = retractions = reversals = edits = 0
    for event in primary:
        prior, value = event["previous"], event["value"]
        if event["kind"] == "feedback":
            continue
        if prior == value:
            edits += int(event["kind"] == "label_state")
            continue
        transitions[prior or "unknown"][value or "retracted"] += 1
        changes += int(prior in LABEL_CLASSES and value in LABEL_CLASSES)
        retractions += int(prior in LABEL_CLASSES and value is None)
        reversals += int(prior in LABEL_CLASSES and value in LABEL_CLASSES
                         and is_positive_priority(prior) != is_positive_priority(value))
    return {"status": status, "raw_observations": len(events), "unique_papers": len({e["paper_key"] for e in primary}),
            "latest_conflicting_papers": conflicts, "class_support": support(matched, classes),
            "agreement": calibration, "agreement_ci": ci, "ordinal_error": ordinal_errors(matched),
            "transitions": {key: dict(value) for key, value in sorted(transitions.items())},
            "category_changes_lower_bound": changes if channel == "journal" else None,
            "retractions_lower_bound": retractions if channel == "journal" else None,
            "reversals_lower_bound": reversals if channel == "journal" else None, "comment_edits": edits,
            "recorded_transition_observations": {key: dict(value) for key, value in sorted(transitions.items())},
            "binary_feedback_class_support": support(feedback, FEEDBACK_CLASSES),
            "overlap_policy": "label_observations_preferred_over_binary_for_same_paper_not_added",
            "disagreements": [e["observation_id"] for e in matched if _disagrees(e)],
            "by_source": dict(Counter(e["source"] or "unknown" for e in matched)),
            "by_surface": dict(Counter(e["surface"] or "unknown" for e in matched)),
            "cohort": "latest_unique_observed_explicit_paper_within_window",
            "inference": "selection_biased_descriptive_not_unbiased_improvement_or_sufficiency"}


def periods(events, cutoff, tables, sources, guard):
    result = {}
    for name, days in WINDOWS.items():
        guard.check()
        selected = [e for e in events if within(e, cutoff, days)]
        output = {}
        for channel in ("journal", "interaction_log", "current_feedback"):
            if channel == "journal":
                status = tables["sync_changes"]["status"]
            elif channel == "interaction_log":
                status = sources[channel]["status"]
            else:
                status = "available_current_snapshot_only"
            output[channel] = channel_metrics([e for e in selected if e["channel"] == channel], status, channel)
        outcomes = [e for e in selected if e["kind"] == "outcome"]
        output["outcomes"] = {"counts": dict(Counter(e["value"] for e in outcomes)),
                              "unique_papers": len({e["paper_key"] for e in outcomes}),
                              "preference_labels": False}
        output["topics"] = {"status": "unscorable", "reason": "historical_recorded_topic_categories_unavailable"}
        result[name] = output
    return result


def chronological(events, papers, cutoff, split_at):
    eligible = [e for e in events if e["eligibility"] == "eligible" and e["kind"] in {
        "label_state", "label_transition", "feedback"} and e["value"] is not None]
    first = {}
    for event in eligible:
        paper = event["paper_key"]
        first[paper] = min(first.get(paper, cutoff), aware_time(event["time"]))
    earlier = sorted(p for p, when in first.items() if when < split_at)
    earlier_set = set(earlier)
    later = sorted(p for p, when in first.items() if when >= split_at)
    # No later observation of an earlier paper enters the later partition.
    locators = [e["observation_id"] for e in eligible
                if (aware_time(e["time"]) < split_at) == (e["paper_key"] in earlier_set)]
    return {"status": "eligibility_only", "split_at": split_at.isoformat().replace("+00:00", "Z"),
            "earlier_papers": earlier, "later_papers": later, "eligible_observation_locators": locators,
            "assignment": "first_eligible_observation_per_current_canonical_family",
            "alias_history": "current_mapping_only", "historical_features": "unavailable",
            "scored_evaluation": None, "inference_model_identity": None,
            "excluded_observations": len(events) - len(locators), "paper_groups": len(papers)}
