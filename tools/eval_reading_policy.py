#!/usr/bin/env python
"""Evaluate the production reading cap against a frozen, human-labelled fixture.

Run: ``uv run python tools/eval_reading_policy.py --check``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from zotero_summarizer.services.library.review_fleet.propose import effective_read_decision

DEFAULT_FIXTURE = Path(__file__).with_name("reading_policy_fixture.json")


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for row in rows:
        signals = row["signals"]
        raw = str(signals["digest"].get("read_decision") or "")
        effective, flags = effective_read_decision(
            signals["digest"], signals.get("quality"),
            goal_summaries=signals.get("goal_summaries"),
        )
        results.append({**row, "raw": raw, "effective": effective, "flags": flags})
    reads = [row for row in results if row["effective"] == "read"]
    ideas = [row for row in results if row["idea_worth_preserving"]]
    metrics = {
        "papers": len(results),
        "baseline_read_rate": round(sum(row["raw"] == "read" for row in results) / len(results), 3),
        "policy_read_rate": round(len(reads) / len(results), 3),
        "read_precision": round(sum(row["expected_read_decision"] == "read" for row in reads) / len(reads), 3) if reads else None,
        "idea_rescue_recall": round(sum(row["effective"] != "skip" for row in ideas) / len(ideas), 3),
        "high_friction_full_reads": sum(row["writing_friction"] == "high" for row in reads),
        "weak_evidence_full_reads": sum(row["scientific_concern"] == "yes" for row in reads),
        "exact_action_accuracy": round(sum(row["effective"] == row["expected_read_decision"] for row in results) / len(results), 3),
    }
    metrics["passes"] = bool(
        metrics["read_precision"] is not None
        and metrics["read_precision"] >= 0.8
        and metrics["idea_rescue_recall"] >= 0.9
        and metrics["policy_read_rate"] < metrics["baseline_read_rate"]
        and metrics["high_friction_full_reads"] == 0
        and metrics["weak_evidence_full_reads"] == 0
    )
    return {"metrics": metrics, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    report = evaluate(payload["papers"])
    print(json.dumps({"fixture": payload["fixture"], **report["metrics"]}, indent=2))
    return int(args.check and not report["metrics"]["passes"])


if __name__ == "__main__":
    raise SystemExit(main())
