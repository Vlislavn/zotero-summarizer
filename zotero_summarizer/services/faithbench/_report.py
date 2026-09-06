"""Report stage: render ``calculate_statistics`` output to JSON + Markdown and
append a headline line to the master ``faithbench-runs.jsonl`` log.

Both artifacts read the *same* stats dict — no number is recomputed for the
text view, so the human-readable report can never drift from the machine one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zotero_summarizer.services import run_log
from zotero_summarizer.services._common import now_iso_z
from zotero_summarizer.services.faithbench._dataset import (
    BenchmarkItem,
    BenchmarkMeta,
    QAItem,
    load_benchmark,
    _validate_item,
    _benchmark_sha,
)
from zotero_summarizer.services.faithbench._build_claims import _claim_rows
from zotero_summarizer.services.faithbench._corpus import load_frozen_text
from zotero_summarizer.services.faithbench._runner import (
    RunOptions, RunPaths, latest_by_key, load_jsonl, trial_key, _load_manifest, _response_sha,
)
from zotero_summarizer.services.faithbench._stats import calculate_statistics

MASTER_LOG_NAME = "faithbench-runs.jsonl"


def _checked_judgments(manifest: dict, meta: BenchmarkMeta, items: list[BenchmarkItem],
                       responses: list[dict], judgments: list[dict]) -> list[dict]:
    options = RunOptions(conditions=tuple(manifest["conditions"]), tracks=tuple(manifest["tracks"]),
                         runs=manifest["runs"], limit=manifest.get("limit"))
    expected = {
        ("qa", item.item_id, condition, run) for item in items[:options.limit]
        for condition in options.conditions for run in range(1, options.runs + 1)
    } if "qa" in options.tracks else set()
    if "claims" in options.tracks:
        expected.update(("claims", f"claims:{p.item_key}", "digest", run)
                        for p in meta.papers for run in range(1, options.runs + 1))
    for row in responses + judgments:
        if row.get("run_id") != manifest["run_id"] or type(row.get("run_number")) is not int:
            raise ValueError("Trial identity does not match the run manifest")
    current = {(row["track"], *trial_key(row)): row for row in responses}
    if not expected or set(current) != expected:
        raise ValueError("Incomplete or unexpected response coverage; finish the configured run before reporting")
    expected_judgments = set()
    for key, row in current.items():
        indices = [None]
        if row["track"] == "claims" and row.get("status") == "ok":
            parsed = row.get("parsed")
            try:
                indices = list(range(len(_claim_rows(parsed.get("claims") if isinstance(parsed, dict) else None))))
            except ValueError:
                indices = [None]  # Malformed output has one explicit failed-trial judgment, not zero rows.
        expected_judgments.update((key, index) for index in indices)
    latest = {}
    for row in judgments:
        key = (row["track"], *trial_key(row))
        if key not in current or row.get("response_sha256") != _response_sha(current[key]):
            continue  # Earlier attempts remain history, not evidence for the current response.
        index = row.get("claim_idx")
        if index is not None and type(index) is not int:
            raise ValueError("Claim index must be an integer or null")
        latest[(key, index)] = row
    if set(latest) != expected_judgments:
        raise ValueError("Incomplete or stale judgments; run faithbench judge before reporting")
    context = next(iter(latest.values())).get("judge_context")
    if (not isinstance(context, dict) or context.get("benchmark_sha256") != _benchmark_sha(meta, items)
            or context.get("paper_faults") or context.get("item_faults")
            or any(row.get("judge_context") != context for row in latest.values())):
        raise ValueError("Mixed or stale judging context; finish re-judging the intact benchmark")
    return list(latest.values())


def items_meta(items: list[BenchmarkItem]) -> dict[str, dict[str, str]]:
    return {
        item.item_id: {
            "kind": item.kind,
            "answer_type": item.answer_type if isinstance(item, QAItem) else "",
        }
        for item in items
    }


def build_report(
    *,
    paths: RunPaths,
    faithbench_dir: Path,
) -> dict[str, Any]:
    """Assemble + persist report.json / report.md; returns the report dict."""
    manifest = _load_manifest(paths)
    benchmark_path = Path(manifest["benchmark_path"])
    meta, items = load_benchmark(benchmark_path, expected_sha256=manifest["benchmark_sha256"])
    texts = {paper.item_key: load_frozen_text(faithbench_dir / "papers", paper.item_key,
                                             expected_sha256=paper.text_sha256) for paper in meta.papers}
    for item in items:
        _validate_item(item, meta, texts)
    responses = list(latest_by_key(load_jsonl(paths.responses)).values())
    judgments = load_jsonl(paths.judgments)
    if not judgments:
        raise RuntimeError(
            f"no judgments in {paths.judgments}; run `faithbench judge` before `report`"
        )
    judgments = _checked_judgments(manifest, meta, items, responses, judgments)
    stats = calculate_statistics(responses, judgments, items_meta(items))

    judge_models = sorted({str(j["judge_model"]) for j in judgments if j.get("judge_model")})
    report = {
        "run_id": manifest.get("run_id"),
        "timestamp": now_iso_z(),
        "git_commit": manifest.get("git_commit", ""),
        "benchmark_path": str(benchmark_path),
        "benchmark_sha256": manifest.get("benchmark_sha256", ""),
        "model_under_test": {
            "model": manifest.get("model"),
            "provider": manifest.get("provider_name"),
            "base_url": manifest.get("base_url"),
        },
        "judge": {"models_used": judge_models, "context": judgments[0]["judge_context"]},
        "num_runs": manifest.get("runs"),
        "conditions": manifest.get("conditions"),
        "tracks": manifest.get("tracks"),
        **stats,
    }

    paths.report_json.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    paths.report_md.write_text(render_markdown(report), encoding="utf-8")

    headline: dict[str, Any] = {
        "run_id": report["run_id"],
        "timestamp": report["timestamp"],
        "git_commit": report["git_commit"],
        "benchmark_sha256": report["benchmark_sha256"],
        "model": manifest.get("model"),
        "judge_models": judge_models,
        "totals": report["totals"],
    }
    for condition, block in (report["tracks"].get("qa") or {}).items():
        headline[f"qa_{condition}_accuracy"] = block["accuracy"]["mean"]
        headline[f"qa_{condition}_accuracy_median"] = block["accuracy"]["median"]
        headline[f"qa_{condition}_trap_hallucination_rate"] = block["trap"]["hallucination_rate"]
    claims_block = (report["tracks"].get("claims") or {}).get("digest")
    if claims_block:
        headline["claims_support_rate"] = claims_block["support_rate"]["mean"]
        headline["claims_support_rate_median"] = claims_block["support_rate"]["median"]
    run_log.append_run(faithbench_dir / MASTER_LOG_NAME, headline)
    return report


# ---------------------------------------------------------------------------
# Markdown rendering (formatting only — every number comes from the stats dict)
# ---------------------------------------------------------------------------


def _fmt_pct(x: float | None) -> str:
    return "N/A (unmeasured)" if x is None else f"{100.0 * x:.1f}%"


def _qa_section(condition: str, block: dict[str, Any]) -> list[str]:
    acc = block["accuracy"]
    lines = [
        f"### QA — `{condition}`",
        "",
        f"- Accuracy: **{_fmt_pct(acc['mean'])}** ± {_fmt_pct(acc['sem_across_runs'])} "
        f"(STD {_fmt_pct(acc['std_across_runs'])}) over {block['n_validated']} validated trials "
        f"({block['n_unjudgeable']} unjudgeable excluded, {block['n_harness_faults']} harness faults)",
        f"- Answerable-only accuracy: {_fmt_pct(block['answerable_accuracy'])}; "
        f"wrong-abstain rate: {_fmt_pct(block['wrong_abstain_rate'])}",
        f"- **Trap hallucination rate: {_fmt_pct(block['trap']['hallucination_rate'])}** "
        f"({block['trap']['n_trap_trials']} trap trials; abstention precision "
        f"{_fmt_pct(block['trap']['abstention_precision'])}, recall "
        f"{_fmt_pct(block['trap']['abstention_recall'])})",
        f"- Judge escalation: {_fmt_pct(block['judge_escalation_fraction'])} of validated trials",
        f"- Latency: p50 {block['latency']['p50']}s, p90 {block['latency']['p90']}s, "
        f"mean {block['latency']['mean']}s (n={block['latency']['n']}, "
        f"total {block['latency']['total_wall_seconds']}s)",
    ]
    if "pass_at_k" in block:
        lines.append(
            f"- Pass@{block['k']}: {_fmt_pct(block['pass_at_k'])}; "
            f"Pass^{block['k']}: {_fmt_pct(block['pass_hat_k'])}"
        )
    if block["by_answer_type"]:
        per_type = ", ".join(
            f"{t}: {_fmt_pct(b['accuracy'])} (n={b['n']})"
            for t, b in sorted(block["by_answer_type"].items())
        )
        lines.append(f"- By answer type: {per_type}")
    if block["failure_reasons"]:
        reasons = ", ".join(f"{k}: {v}" for k, v in sorted(block["failure_reasons"].items()))
        lines.append(f"- Failure reasons: {reasons}")
    lines.append("")
    return lines


def _claims_section(block: dict[str, Any]) -> list[str]:
    sr = block["support_rate"]
    lines = [
        "### Review claims — `digest`",
        "",
        f"- Claim support rate: **{_fmt_pct(sr['mean'])}** ± {_fmt_pct(sr['sem_across_runs'])} "
        f"(STD {_fmt_pct(sr['std_across_runs'])}) over {block['n_validated']} validated claims "
        f"({block['n_unjudgeable']} unjudgeable excluded)",
        f"- Digest latency: p50 {block['latency']['p50']}s, mean {block['latency']['mean']}s "
        f"(n={block['latency']['n']})",
    ]
    if block["by_field"]:
        per_field = ", ".join(
            f"{f}: {_fmt_pct(b['support_rate'])} (n={b['n']})"
            for f, b in sorted(block["by_field"].items())
        )
        lines.append(f"- By digest field: {per_field}")
    if block["top_unsupported"]:
        lines.append("- Top unsupported claims:")
        for entry in block["top_unsupported"]:
            lines.append(f"  - `{entry['item_id']}#{entry['claim_idx']}`: {entry['details']}")
    lines.append("")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# faithbench report — {report.get('run_id')}",
        "",
        f"- Timestamp: {report.get('timestamp')}  |  Git: `{report.get('git_commit') or 'n/a'}`",
        f"- Model under test: `{report['model_under_test'].get('model')}` "
        f"via {report['model_under_test'].get('base_url')}",
        f"- Judge model(s): {', '.join(f'`{m}`' for m in report['judge']['models_used']) or '(no escalations)'}",
        f"- Benchmark: `{report.get('benchmark_path')}` (sha {str(report.get('benchmark_sha256'))[:12]})",
        f"- Runs per item: {report.get('num_runs')}  |  Conditions: {report.get('conditions')}",
        "",
        "## Totals",
        "",
        f"- {report['totals']['n_response_trials']} trials → {report['totals']['n_judgments']} judgments "
        f"({report['totals']['n_validated']} validated, {report['totals']['n_unjudgeable']} unjudgeable, "
        f"{report['totals']['n_harness_faults']} harness faults)",
        "",
    ]
    for condition, block in (report["tracks"].get("qa") or {}).items():
        lines.extend(_qa_section(condition, block))
    claims_block = (report["tracks"].get("claims") or {}).get("digest")
    if claims_block:
        lines.extend(_claims_section(claims_block))
    return "\n".join(lines) + "\n"
