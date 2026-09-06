#!/usr/bin/env python
"""Offline evaluation for the production Research Intelligence projections.

Run: ``uv run python tools/eval_research_feed.py --check``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

from zotero_summarizer.models import ResearchCandidate, ResearchProfile
from zotero_summarizer.services.research_feed.card import build_card
from zotero_summarizer.services.research_feed.profile import DEFAULT_PROJECTS, DEFAULT_THEMES
from zotero_summarizer.services.research_feed.runner import triage_candidate
from zotero_summarizer.services.library.review_fleet.propose import effective_read_decision

FIXTURE = Path(__file__).with_name("research_feed_fixture.json")
READING_FIXTURE = Path(__file__).with_name("reading_policy_fixture.json")


def _case(row, profile):
    candidate = ResearchCandidate(
        source_id=row["id"], source="fixture", title=row["title"],
        abstract="; ".join(row["projects"]), url=f"https://example.test/{row['id']}",
    )
    summary = {"executive_summary": candidate.abstract, "methods": "evaluation benchmark"}
    prior = {
        "decision": row["decision"], "reading_priority": "could_read",
        "composite_score": 2.4, "shap_contribs_json": json.dumps({"summary": summary}),
    }
    triage = triage_candidate(candidate, prior, profile)
    artifact = row.get("verified_code_url")
    review = {
        "digest": {"tldr": row["title"], "methods": "evaluation benchmark",
                   "implementation": [], "relevance": candidate.abstract,
                   "read_decision": "skim", "basis": "full_text", "novelty": 3,
                   "significance": 3},
        "quality": {"quality_band": "neutral", "missing_critical": [], "red_flags": []},
        "goal_summaries": [{"relevant": triage.include}],
        "code_link": ({"found": True, "exists": True, "relevance": "matched", "url": artifact}
                      if artifact else {"found": False}),
    }
    card = build_card(candidate, triage, review, profile)
    return row | {"predicted_include": triage.include, "reported_urls": card.code_urls,
                  "project_use": card.project_uses}


def evaluate(payload):
    started = perf_counter()
    profile = ResearchProfile(themes=DEFAULT_THEMES, projects=DEFAULT_PROJECTS)
    rows = [_case(row, profile) for row in payload["papers"]]
    ranked = [row for row in rows if row["predicted_include"]][:10]
    must = [row for row in rows if row["must_not_miss"]]
    reported = [url for row in rows for url in row["reported_urls"]]
    verified = {row["verified_code_url"] for row in rows if row.get("verified_code_url")}
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
        "shortlist_precision_at_10": sum(row["human_include"] for row in ranked) / len(ranked),
        "must_not_miss_recall": sum(row["predicted_include"] for row in must) / len(must),
        "read_skim_skip_agreement": round(reading_matches / len(reading_rows), 3),
        "artifact_availability_accuracy": sum(
            bool(row["reported_urls"]) == bool(row.get("verified_code_url")) for row in rows
        ) / len(rows),
        "reported_code_link_precision": sum(url in verified for url in reported) / len(reported),
        "fabricated_urls": sorted(set(reported) - verified),
        "project_use_coverage": sum(
            row["project_use"] != ["not_applicable"] for row in rows if row["human_include"]
        ) / sum(row["human_include"] for row in rows),
        "estimated_review_minutes": len(ranked) * 2.5,
        "runtime_seconds": round(perf_counter() - started, 4),
        "llm_tokens": 0, "llm_cost": 0,
    }
    metrics["passes"] = bool(
        len(rows) >= 30 and metrics["shortlist_precision_at_10"] >= 0.8
        and metrics["must_not_miss_recall"] == 1
        and metrics["reported_code_link_precision"] >= 0.9
        and not metrics["fabricated_urls"] and metrics["estimated_review_minutes"] <= 30
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
