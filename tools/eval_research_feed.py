#!/usr/bin/env python
"""Offline evaluation for the production Research Intelligence projections.

Run: ``uv run python tools/eval_research_feed.py --check``.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from urllib.parse import urlsplit
from time import perf_counter

from zotero_summarizer.models import ResearchCandidate, ResearchProfile
from zotero_summarizer.services.research_feed.profile import DEFAULT_PROJECTS, DEFAULT_THEMES
from zotero_summarizer.services.research_feed.runner import triage_candidate
from zotero_summarizer.services.library.review_fleet.propose import effective_read_decision

FIXTURE = Path(__file__).with_name("research_feed_fixture.json")
READING_FIXTURE = Path(__file__).with_name("reading_policy_fixture_v2.json")


def _case(row, profile):
    machine = row.get("frozen_machine")
    if machine is not None and not isinstance(machine, dict):
        raise ValueError(f"malformed frozen_machine for {row['id']}")
    required = ("abstract", "source_url", "composite_score", "reading_priority", "summary")
    missing = [key for key in required if machine is None or machine.get(key) is None]
    if machine is not None and not missing:
        url = urlsplit(machine["source_url"]) if isinstance(machine["source_url"], str) else None
        score = machine["composite_score"]
        if (not isinstance(machine["abstract"], str) or not machine["abstract"].strip()
                or not url or url.scheme not in {"http", "https"} or not url.hostname
                or isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not 1 <= score <= 5
                or machine["reading_priority"] not in {"must_read", "should_read", "could_read", "dont_read"}
                or not isinstance(machine["summary"], dict)):
            raise ValueError(f"malformed frozen_machine for {row['id']}")
        projected = {
            "composite_score": machine["composite_score"],
            "reading_priority": machine["reading_priority"],
            "shap_contribs_json": json.dumps({"summary": machine["summary"]}),
        }  # Never inject a post-human 'decision' into the model's projection.
    else:
        machine = None  # explicitly unscorable; never promote null evidence to a measured zero
        projected = None
    candidate = ResearchCandidate(
        source_id=row["id"], source="fixture", title=row["title"],
        abstract=str(machine["abstract"] if machine else ""),
        url=str(machine["source_url"] if machine else f"https://example.test/{row['id']}"),
    )
    triage = triage_candidate(candidate, projected, profile)
    return row | {"predicted_include": triage.include, "predicted_score": triage.score,
                  "predicted_confidence": triage.confidence,
                  "frozen_input_ok": machine is not None, "missing_frozen_fields": missing}


def evaluate(payload):
    started = perf_counter()
    profile = ResearchProfile(themes=DEFAULT_THEMES, projects=DEFAULT_PROJECTS)
    rows = [_case(row, profile) for row in payload["papers"]]
    scorable = all(row["frozen_input_ok"] for row in rows)
    ranked = sorted((row for row in rows if row["predicted_include"]),
                    key=lambda row: (row["predicted_score"], row["predicted_confidence"],
                                     row["title"], row["id"]), reverse=True)[:10] if scorable else []
    must = [row for row in rows if row["must_not_miss"]]
    reading_rows = json.loads(READING_FIXTURE.read_text())["papers"]
    reading_matches = 0
    for row in reading_rows:
        signals = row["signals"]
        action, _flags = effective_read_decision(
            signals["digest"], signals.get("quality"),
            goal_summaries=signals.get("goal_summaries"),
        )
        reading_matches += action == row["expected_read_decision"]
    metrics = {
        "papers": len(rows),
        "inclusion_basis": "frozen_machine" if scorable else "unscorable_missing_frozen_machine",
        "unscorable_ids": [row["id"] for row in rows if not row["frozen_input_ok"]],
        "unscorable_fields": {row["id"]: row["missing_frozen_fields"] for row in rows
                              if not row["frozen_input_ok"]},
        "shortlist_precision_at_10": (sum(row["human_include"] for row in ranked) / len(ranked)
                                      if ranked else 0.0) if scorable else None,
        "must_not_miss_recall": (sum(row["predicted_include"] for row in must) / len(must)
                                 if must else 0.0) if scorable else None,
        "read_skim_skip_agreement": None,  # no frozen reviews for these 30 feed candidates
        "reading_policy_fixture_agreement": round(reading_matches / len(reading_rows), 3),
        "artifact_basis": "unscorable_missing_frozen_review",
        "artifact_availability_accuracy": None,
        "reported_code_link_precision": None,
        "fabricated_urls": None,
        "project_use_coverage": None,  # cards use synthetic rather than frozen review outputs
        "estimated_review_minutes": None,  # no measured human review-time input
        "runtime_seconds": None,
        "evaluator_runtime_seconds": round(perf_counter() - started, 4),
        "llm_tokens": None, "llm_cost": None,
    }
    metrics["passes"] = bool(
        scorable and len(rows) >= 30 and metrics["shortlist_precision_at_10"] >= 0.8
        and metrics["must_not_miss_recall"] == 1
        and metrics["read_skim_skip_agreement"] is not None
        and metrics["read_skim_skip_agreement"] >= 0.8
        and metrics["reported_code_link_precision"] is not None
        and metrics["reported_code_link_precision"] >= 0.9
        and metrics["estimated_review_minutes"] is not None
        and metrics["estimated_review_minutes"] <= 30
    )
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    metrics = evaluate(payload)
    print(json.dumps({"fixture": payload["fixture"], **metrics}, indent=2))
    return int(args.check and not metrics["passes"])


if __name__ == "__main__":
    raise SystemExit(main())
