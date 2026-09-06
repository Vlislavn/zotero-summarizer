"""Per-user calibration — derive system-owned knobs for THIS setup, save to
``data/calibration.json`` (precedence sits between goals.yaml and ZS_* env; see
``services/config_overrides.apply_calibration``).

Tier 1 (this module): a CHEAP first-run ENV PROBE — one bounded completion against the
deep-review endpoint measures throughput and picks the lean-vs-full text/consistency
profile (auto-detecting what the manual ``provider.lean_deep_review`` flag did by hand).
No labels needed; safe to run early. Heavier tiers (opt-in LLM sweeps, continuous
data-driven recal) live in their own modules and reuse ``upsert_calibration_entries``.

Envelope: ``{"version": 1, "updated_at": ..., "tier1": {...}, "entries": {"<dotted.path>":
{"value": V, <receipt>}}}``. Only ``entries[*].value`` is applied to the config; the rest
is provenance.
"""
from __future__ import annotations

import json
import os
import subprocess
from statistics import mean
from time import perf_counter
from pathlib import Path
from typing import Any, Callable

from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import LOGGER, now_iso_z, read_json_or_empty, to_text, write_json_atomic

# Throughput (approx tokens/sec) below which the endpoint is "lean" — a slow local
# model uses the cheaper text/consistency caps so a deep review stays usable. A fast
# remote API endpoint clears this easily → full profile. Named constant
# + env override (no magic number): ZS_CALIBRATION_LEAN_TPS.
_DEFAULT_LEAN_TPS = 25.0
_PROBE_PROMPT = "Write one concise 80-word paragraph about reproducibility in machine learning."


def lean_tps_threshold() -> float:
    raw = os.environ.get("ZS_CALIBRATION_LEAN_TPS")
    return float(raw) if raw is not None else _DEFAULT_LEAN_TPS


def decide_profile(tokens_per_sec: float) -> str:
    """'lean' for a slow endpoint, 'full' for a fast one. Pure (testable)."""
    return "lean" if tokens_per_sec < lean_tps_threshold() else "full"


def measure_throughput(llm: Any, *, prompt: str = _PROBE_PROMPT) -> float:
    """Approx tokens/sec from ONE bounded completion (≈4 chars/token). The single LLM
    call hits the configured deep-review endpoint — remote = memory-safe."""
    start = perf_counter()
    output = to_text(llm.prompt(prompt))
    elapsed = max(perf_counter() - start, 1e-6)
    approx_tokens = max(len(output) / 4.0, 1.0)
    return approx_tokens / elapsed


def read_calibration(calibration_path: Path) -> dict[str, Any]:
    """The current calibration envelope, or ``{}`` when absent (Tier-0 floor)."""
    return read_json_or_empty(calibration_path)


def upsert_calibration_entries(
    calibration_path: Path,
    entries: dict[str, dict[str, Any]],
    *,
    meta_key: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge ``entries`` into ``calibration.json`` (atomic), optionally stamping an
    envelope-level ``meta_key`` (e.g. 'tier1'/'tier3') for idempotency/provenance.
    Reused by every tier so the artifact has one writer shape."""
    payload = read_calibration(calibration_path) or {"version": 1, "entries": {}}
    payload.setdefault("entries", {}).update(entries)
    if meta_key is not None:
        payload[meta_key] = meta
    payload["updated_at"] = now_iso_z()
    write_json_atomic(calibration_path, payload)
    return payload


# --- Tier 3: continuous data-driven recalibration ----------------------------
_DEFAULT_TIER3_MIN_LABELS = 200  # never recalibrate the classifier below this (Tier-0 stands)


def count_golden_labels(settings: Any) -> int:
    """How many golden labels exist — the gate for data-driven recal."""
    from zotero_summarizer.services._common import load_golden_rows

    if not settings.golden_csv_path.exists():
        return 0
    return len(load_golden_rows(settings.golden_csv_path))


def tier3_recalibrate(
    settings: Any,
    *,
    run_eval: Callable[[Any], dict[str, dict[str, float]]],
    min_labels: int | None = None,
    metric: str = "oof_spearman",
) -> dict[str, Any]:
    """Continuous, data-driven recal: when the golden set is large enough, pick the
    classifier that best fits the USER's OWN labels and persist it to calibration.json.
    LABEL-GATED — below ``min_labels`` the Tier-0 default stands (cold-start safe).
    ``run_eval(settings) -> {classifier_name: {<metric>: float}}`` is injected (the CLI
    supplies the real eval_baseline runner — user-coordinated, memory-safe window; tests
    stub it). Heavier tiers piggyback the golden-CSV growth, not a boot loop."""
    min_labels = _DEFAULT_TIER3_MIN_LABELS if min_labels is None else min_labels
    n_labels = count_golden_labels(settings)
    if n_labels < min_labels:
        return {"status": "skipped", "reason": f"only {n_labels} labels (< {min_labels})", "labels": n_labels}
    results = run_eval(settings)
    if not results:
        raise ValueError("tier3 run_eval returned no classifier results")
    winner = max(results, key=lambda c: results[c].get(metric, float("-inf")))
    scores = {c: round(results[c].get(metric, 0.0), 4) for c in results}
    receipt = {"harness": "tier3_eval_baseline", "metric": metric, "labels": n_labels,
               "scores": scores, "ts": now_iso_z()}
    entries = {"classifier_gate.model_name": {"value": winner, **receipt}}
    upsert_calibration_entries(settings.calibration_path, entries, meta_key="tier3",
                               meta={"winner_classifier": winner, **receipt})
    LOGGER.info("Tier-3 recal: winner=%s on %s (%d labels); scores=%s", winner, metric, n_labels, scores)
    return {"status": "calibrated", "winner_classifier": winner, "labels": n_labels, "scores": scores}


def run_full_calibration(
    settings: Any, *, item_keys: list[str] | None = None, papers_limit: int = 3,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """"Calibrate to my setup" — the FOREGROUND, memory-safe orchestrator (CLI + API
    entrypoint). Runs Tier-1 (env probe, idempotent) then Tier-2 (text-budget sweep) on
    the user's resolved deep-review endpoint, writing both to calibration.json. Label-free.
    Lazy imports keep the setup package's load light + avoid a library-layer cycle.
    ``progress`` (optional) receives ``{phase, completed, total}`` dicts for a live UI."""
    from zotero_summarizer.models.providers import resolve_stage
    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.library import quality_review
    from zotero_summarizer.services.llm.factory import build_client_for_provider

    papers = load_review_papers(settings, item_keys=item_keys, limit=papers_limit)
    if not papers:
        raise RuntimeError("No built paper briefs to calibrate on; build a paper brief first")
    config = read_config(settings.config_path, settings.calibration_path)
    resolved = resolve_stage(config.llm_routing, "deep_review")
    provider, model = resolved.provider, resolved.model
    LOGGER.info("Calibration: deep_review endpoint %s/%s @ %s (local=%s)",
                provider.name, model, provider.base_url, provider.is_local)

    if progress is not None:
        progress({"phase": "measuring endpoint", "completed": 0, "total": 0})
    # Remote endpoints are always 'full' (no probe); only a local provider is measured.
    probe_llm = build_client_for_provider(provider, model, enable_thinking=False) if provider.is_local else None
    tier1 = tier1_env_calibrate(config, settings.calibration_path, llm=probe_llm, is_local=provider.is_local)
    if provider.is_local:
        memory_preflight()  # remote endpoints skip this — they load no local model
    digest_llm = build_client_for_provider(provider, model, enable_thinking=provider.thinking_on)

    def run_digest(title: str, text: str, budget: int) -> tuple[Any, float]:
        start = perf_counter()
        digest = quality_review.assess_digest(title=title, full_text=text, config=config, llm=digest_llm, max_chars=budget)
        return digest, perf_counter() - start

    tier2 = tier2_calibrate(config, settings.calibration_path, papers, run_digest=run_digest, progress=progress)
    return {"tier1": tier1, "tier2": tier2, "provider": provider.name, "model": model, "papers": len(papers)}


# --- Tier 2: opt-in LLM sweep ("Calibrate to my setup") ----------------------
# Substantive digest fields — the deterministic completeness metric (no judge needed:
# completeness = fraction of these that came back non-empty), mirroring bench_deep_review.
_DIGEST_SUBSTANTIVE_FIELDS = (
    "tldr", "verdict", "executive_summary", "key_findings",
    "methods", "limitations", "key_strength", "key_weakness",
)
_DEFAULT_COMPLETENESS_TOLERANCE = 0.05  # accept a smaller budget within 5% of the best


def digest_completeness(digest: Any) -> float:
    """Fraction of substantive digest fields that are non-empty (deterministic — no LLM judge)."""
    filled = []
    for field in _DIGEST_SUBSTANTIVE_FIELDS:
        value = getattr(digest, field, None)
        if isinstance(value, str):
            filled.append(bool(value.strip()))
        elif isinstance(value, (list, tuple)):
            filled.append(len(value) > 0)
        else:
            filled.append(value is not None)
    return sum(filled) / len(filled) if filled else 0.0


def swap_used_mb() -> float:
    """macOS swap-used in MB (the 'box already over-committed' signal — free-phys% reads
    falsely healthy because it counts reclaimable inactive memory)."""
    # e.g. "vm.swapusage: total = 7168.00M  used = 5635.50M  free = 1532.50M (encrypted)"
    out = subprocess.run(["sysctl", "vm.swapusage"], capture_output=True, text=True, check=True).stdout
    tokens = out.replace("=", " ").split()
    return float(tokens[tokens.index("used") + 1].rstrip("M"))


def memory_preflight(*, max_swap_mb: float = 6000.0) -> None:
    """Refuse to start a LOCAL-model calibration when swap is already over-committed
    (a local model load would thrash before the sweep finishes). Caller invokes this
    ONLY for a local provider — a remote endpoint loads no local model,
    so existing swap from other apps must not block it. Memory-safe-runs rule."""
    used_mb = swap_used_mb()
    if used_mb > max_swap_mb:
        raise RuntimeError(
            f"ABORT pre-flight — swap already {used_mb:.0f}MB (> {max_swap_mb}MB); a local model "
            "load would thrash. Free RAM / use the remote API endpoint, then re-run."
        )


def load_review_papers(
    settings: Any, *, item_keys: list[str] | None = None, limit: int = 3
) -> list[tuple[str, str]]:
    """Source (title, full_text) from already-built paper briefs
    (``data/paper_render/<key>/paper_read.json``). Calibration is LABEL-FREE — it only
    needs real paper text, not user verdicts."""
    if not 1 <= limit <= 10:
        raise APIError("validation_error", "Calibration requires 1 to 10 papers", 422)
    render_dir = settings.paper_render_dir.resolve()
    keys = item_keys
    if keys is None:
        keys = sorted(p.name for p in render_dir.iterdir() if p.is_dir()) if render_dir.exists() else []
    paths = []
    for key in dict.fromkeys(keys):
        if not key.strip() or key in {".", ".."} or any(c in key for c in "/\\\0"):
            raise APIError("validation_error", "Invalid calibration item key", 422)
        path = (render_dir / key / "paper_read.json").resolve()
        if not path.is_relative_to(render_dir):
            raise APIError("validation_error", "Calibration path escapes paper storage", 422)
        paths.append((key, path))
    papers: list[tuple[str, str]] = []
    for key, state_path in paths:
        if not state_path.exists():
            continue
        state = json.loads(state_path.read_text(encoding="utf-8"))
        text = (state.get("qa_text") or "").strip()
        if text:
            papers.append((str(state.get("title") or key), text))
        if len(papers) >= limit:
            break
    return papers


def tier2_calibrate(
    config: GoalsConfig,
    calibration_path: Path,
    papers: list[tuple[str, str]],
    *,
    run_digest: Callable[[str, str, int], tuple[Any, float]],
    budgets: list[int] | None = None,
    tolerance: float | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Sweep the deep-review text budget on the user's OWN endpoint and pick the
    FASTEST budget whose mean digest completeness is within ``tolerance`` of the best,
    tie-breaking toward MORE context. Latency-as-cost auto-adapts: a fast endpoint keeps
    the full budget (same speed, more context → better grounding); a slow one drops to
    lean (faster, no completeness loss). ``run_digest(title, text, budget) -> (digest,
    secs)`` is injected so the LLM call stays at the edge (testable). Writes the winner +
    per-budget receipts to calibration.json under the ``tier2`` stamp."""
    if not papers:
        raise ValueError("tier2_calibrate needs at least one (title, text) paper")
    budgets = budgets or sorted({config.quality_review.lean_max_text_chars, config.quality_review.max_text_chars})
    tolerance = _DEFAULT_COMPLETENESS_TOLERANCE if tolerance is None else tolerance

    total = len(budgets) * len(papers)
    done = 0
    if progress is not None:
        progress({"phase": "reviewing", "completed": 0, "total": total})
    per_budget: dict[int, dict[str, Any]] = {}
    for budget in budgets:
        comps, secs = [], []
        for title, text in papers:
            digest, elapsed = run_digest(title, text, budget)
            comps.append(digest_completeness(digest))
            secs.append(elapsed)
            done += 1
            if progress is not None:
                progress({"phase": f"reviewed {done}/{total} (budget {budget} chars)",
                          "completed": done, "total": total})
        per_budget[budget] = {"completeness_mean": round(mean(comps), 3),
                              "secs_mean": round(mean(secs), 1), "n": len(papers)}

    best = max(m["completeness_mean"] for m in per_budget.values())
    # Among budgets within tolerance of the best completeness, pick the FASTEST;
    # tie-break toward MORE context (larger budget = better grounding at equal speed).
    eligible = [(b, m) for b, m in per_budget.items() if m["completeness_mean"] >= best - tolerance]
    winner = min(eligible, key=lambda bm: (bm[1]["secs_mean"], -bm[0]))[0]
    receipt = {"harness": "tier2_text_budget", "metric": "digest_completeness",
               "per_budget": {str(b): m for b, m in per_budget.items()}, "ts": now_iso_z()}
    entries: dict[str, dict[str, Any]] = {}
    if winner != config.quality_review.max_text_chars:
        entries["quality_review.max_text_chars"] = {"value": winner, **receipt}
    upsert_calibration_entries(calibration_path, entries, meta_key="tier2",
                               meta={"winner_max_text_chars": winner, "best_completeness": round(best, 3), **receipt})
    LOGGER.info("Tier-2 calibration: winner max_text_chars=%d (best completeness %.3f); %d override(s)",
                winner, best, len(entries))
    return {"status": "calibrated", "winner_max_text_chars": winner,
            "per_budget": {str(b): m for b, m in per_budget.items()}, "entries": sorted(entries)}


def tier1_env_calibrate(
    config: GoalsConfig,
    calibration_path: Path,
    *,
    llm: Any = None,
    throughput: float | None = None,
    is_local: bool = True,
) -> dict[str, Any]:
    """First-run env probe → persist the lean/full profile. Idempotent: skips when a
    ``tier1`` stamp already exists. A REMOTE endpoint (``is_local=False``) is
    always 'full' — the lean tier exists only for local prefill-bound models, and a
    short-prompt tokens/sec probe undercounts a reasoning model (most tokens land in
    reasoning_content), so we don't probe remotes. For a local provider, ``throughput``
    (tests) or a live ``llm`` measurement decides lean vs full."""
    existing = read_calibration(calibration_path)
    if "tier1" in existing:
        return {"status": "skipped", "reason": "already calibrated", "tier1": existing["tier1"]}

    if not is_local:
        profile, throughput = "full", None
        receipt = {"harness": "tier1_env_probe", "reason": "remote endpoint (always full)", "ts": now_iso_z()}
    else:
        if throughput is None:
            if llm is None:
                raise ValueError("tier1_env_calibrate needs either a live `llm` client or a `throughput`")
            throughput = measure_throughput(llm)
        profile = decide_profile(throughput)
        receipt = {"harness": "tier1_env_probe", "metric": "tokens_per_sec",
                   "measured": round(throughput, 1), "ts": now_iso_z()}

    entries: dict[str, dict[str, Any]] = {}
    if profile == "lean":
        # Drop the effective budgets to the configured lean caps so a slow endpoint
        # stays usable; a fast endpoint keeps the full defaults (no entries written).
        entries["quality_review.max_text_chars"] = {"value": config.quality_review.lean_max_text_chars, **receipt}
        entries["quality_review.self_consistency_runs"] = {"value": config.quality_review.lean_self_consistency_runs, **receipt}

    upsert_calibration_entries(calibration_path, entries, meta_key="tier1",
                               meta={"profile": profile, **receipt})
    tps_str = "n/a (remote)" if throughput is None else f"{throughput:.1f} tok/s"
    LOGGER.info("Tier-1 calibration: %s profile (%s); wrote %d override(s) to %s",
                profile, tps_str, len(entries), calibration_path)
    return {"status": "calibrated", "profile": profile,
            "tokens_per_sec": None if throughput is None else round(throughput, 1),
            "entries": sorted(entries)}
