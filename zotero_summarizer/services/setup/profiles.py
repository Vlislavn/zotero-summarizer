"""Deployment profiles — fully-local vs hybrid (local + API), and the stage-cost
measurement that informs the choice.

A *profile* is a high-level, user-meaningful decision (Tesler's Law: privacy/cost vs
quality — only the human can make it): which LLM stage runs on the **local** model vs the
**API** remote model, plus the review **depth** for local (superficial vs deep). It expands
into the existing ``llm_routing`` stages + the per-provider ``lean_deep_review`` tier flag
(superficial = lean), so it adds NO new runtime concept — just a curated preset over the
knobs the system already has.

``measure_stage_costs`` answers "which steps are token-heavy / compute-heavy" by running
each stage's real prompt once per provider and recording approx tokens (≈4 chars/token,
since the client Protocol exposes no usage) + wall-clock — so routing the HEAVY stage to
the best-fit provider is data-driven, not guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from zotero_summarizer.models import GoalsConfig

# The two presets. "custom" is not here — it means "leave llm_routing as the user set it"
# (the AI-models routing editor). Each preset maps every stage to "local" or "api" and
# sets the local deep-review depth. Descriptions are shown verbatim in the UI.
PROFILES: dict[str, dict[str, Any]] = {
    "local": {
        "label": "Fully local",
        "description": (
            "Every stage runs on your local model — private and free. Deep reviews are "
            "SUPERFICIAL (a local model is slow on long context), so they read less and "
            "self-check once. Best when privacy matters or you have no API budget."
        ),
        "stages": {"feed": "local", "backlog": "local", "deep_review": "local"},
        "deep_review_depth": "superficial",
    },
    "hybrid": {
        "label": "Hybrid (recommended)",
        "description": (
            "High-volume feed triage + backlog run on your local model (cheap, private); "
            "the DEEP review runs on the remote API model — best quality, and "
            "memory-safe (no local model load). The best balance of cost, speed, quality."
        ),
        "stages": {"feed": "local", "backlog": "local", "deep_review": "api"},
        "deep_review_depth": "deep",
    },
}

_STAGES = ("feed", "backlog", "deep_review")
# Stages we actually cost-measure: feed (the high-volume short call) and deep_review (the
# long, expensive one). backlog mirrors feed's shape, so we don't double-measure it.
_MEASURED_STAGES = ("feed", "deep_review")
_CHARS_PER_TOKEN = 4.0
# When the deep review runs on a REMOTE provider, fan out its within-paper sub-calls
# (the self-consistency rubric runs + per-goal summaries) — a remote API has no host-RAM
# constraint, so serial sub-calls just waste wall-clock. Local stays serial (RAM-safety).
# This is the within-paper analogue of the fleet's cross-paper parallelism, and it
# overrides the local-only TRIAGE_JOB_CONCURRENCY knob (which exists to serialise LOCAL work).
_DEFAULT_REMOTE_SUB_CONCURRENCY = 4


def apply_profile(
    config: GoalsConfig,
    profile_name: str,
    *,
    local_provider: str,
    api_provider: str,
    local_model: str,
    api_model: str,
    local_depth: str | None = None,
) -> GoalsConfig:
    """Return ``config`` with ``llm_routing`` rewritten for the named preset: each stage
    pointed at the local or API provider, and the deep-review provider's ``lean_deep_review``
    set from the depth (superficial→lean→True, deep→full→False). ``local_depth`` overrides
    the preset's local depth (lets a user ask for a slow, DEEP local review). Fails loud if
    a referenced provider isn't in the registry (LLMRoutingConfig re-validates)."""
    from zotero_summarizer.models.providers import LLMRoutingConfig

    if profile_name not in PROFILES:
        raise ValueError(f"unknown profile {profile_name!r}; one of {sorted(PROFILES)}")
    profile = PROFILES[profile_name]
    name_for = {"local": local_provider, "api": api_provider}
    model_for = {"local": local_model, "api": api_model}

    routing = config.llm_routing.model_dump(mode="python")
    for stage in _STAGES:
        which = profile["stages"][stage]
        routing[stage] = {"provider": name_for[which], "model": model_for[which]}

    deep_which = profile["stages"]["deep_review"]
    depth = local_depth or profile["deep_review_depth"]
    deep_provider = name_for[deep_which]
    for provider in routing["providers"]:
        if provider["name"] == deep_provider:
            provider["lean_deep_review"] = depth == "superficial"
            # A remote deep-review provider should fan out its within-paper sub-calls
            # (don't let the local-only TRIAGE_JOB_CONCURRENCY serialise a RAM-free API).
            if deep_which == "api" and not provider.get("max_sub_concurrency"):
                provider["max_sub_concurrency"] = _DEFAULT_REMOTE_SUB_CONCURRENCY

    new_routing = LLMRoutingConfig.model_validate(routing)
    return config.model_copy(update={"llm_routing": new_routing})


def detect_profile(config: GoalsConfig) -> str:
    """Which preset the current routing matches: 'local' (all stages local), 'hybrid'
    (feed local + deep_review remote), or 'custom' (anything else)."""
    routing = config.llm_routing

    def stage_is_local(stage: str) -> bool:
        stage_cfg = getattr(routing, stage)
        name = stage_cfg.provider or routing.default.provider
        return routing.provider_by_name(name).is_local

    feed_local, deep_local = stage_is_local("feed"), stage_is_local("deep_review")
    if feed_local and deep_local:
        return "local"
    if feed_local and not deep_local:
        return "hybrid"
    return "custom"


def _provider_roles(routing: Any) -> tuple[str, str | None]:
    """(local_provider_name, api_provider_name|None) from the routing registry, by is_local."""
    local_name = next((p.name for p in routing.providers if p.is_local), None)
    api_name = next((p.name for p in routing.providers if not p.is_local), None)
    if local_name is None:
        local_name = routing.default.provider  # all-remote setup: 'local' role falls to default
    return local_name, api_name


def set_profile(settings: Any, profile_name: str, *, local_depth: str | None = None) -> dict[str, Any]:
    """Apply a named preset to the user's config (writes llm_routing). Resolves the local/API
    providers from the registry by ``is_local``. The remote deep-review model is REMEMBERED
    (in calibration.json) whenever it's set, so a 'hybrid' restores the remote model even after a
    round-trip through 'local' (which routes deep_review off the API). 'hybrid' fails loud if no
    remote provider exists or no API deep model has ever been chosen."""
    from zotero_summarizer.services._common import read_config, write_user_config
    from zotero_summarizer.services.setup.calibration import read_calibration, upsert_calibration_entries

    config = read_config(settings.config_path, settings.calibration_path)
    routing = config.llm_routing
    local_name, api_name = _provider_roles(routing)

    # Remember the current REMOTE deep-review model before we possibly route it away.
    deep_prov = routing.provider_by_name(routing.deep_review.provider) if routing.deep_review.provider else None
    if deep_prov is not None and not deep_prov.is_local and routing.deep_review.model:
        upsert_calibration_entries(settings.calibration_path, {}, meta_key="deployment",
                                   meta={"api_deep_model": routing.deep_review.model})

    if profile_name == "hybrid" and api_name is None:
        raise ValueError("the 'hybrid' profile needs a remote API provider — add one first")
    local_model = routing.default.model
    api_model = read_calibration(settings.calibration_path).get("deployment", {}).get("api_deep_model")
    if profile_name == "hybrid" and not api_model:
        raise ValueError("no API deep-review model is known — choose one in AI models (or the wizard) before 'hybrid'")

    new_config = apply_profile(
        config, profile_name, local_provider=local_name, api_provider=(api_name or local_name),
        local_model=local_model, api_model=(api_model or local_model), local_depth=local_depth,
    )
    write_user_config(settings.config_path, new_config)
    return {"status": "ok", "profile": profile_name, "local_provider": local_name, "api_provider": api_name,
            "deep_review": PROFILES[profile_name]["stages"]["deep_review"], "api_model": api_model}


def run_full_profile_measure(settings: Any, *, include_local: bool = False) -> dict[str, Any]:
    """Measure feed + deep_review cost per provider (approx tokens + secs) → summary +
    recommendation, persisted to calibration.json under the ``stage_costs`` stamp.

    LOCAL gens are SKIPPED by default — even a 'cheap' feed call loads a multi-GB model that
    can spike swap by gigabytes (measured: a local 8B feed call grew swap +6 GB on a
    constrained box). Pass ``include_local=True`` to measure local too, in a memory-safe
    window (closed apps / real RAM); it's additionally gated on the swap-start threshold.
    Remote providers load nothing and are always measured. The recommendation
    defaults to 'hybrid' when local isn't measured (offload the heavy stage)."""
    from time import perf_counter

    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.library import quality_review
    from zotero_summarizer.services._common import to_text
    from zotero_summarizer.services.llm.factory import build_client_for_provider
    from zotero_summarizer.services.setup.calibration import (
        load_review_papers, swap_used_mb, upsert_calibration_entries,
    )

    # A local gen loads a model into RAM; refuse to start one when swap is already
    # over-committed (the box would thrash). Remote providers load nothing → never gated.
    local_memory_ok = swap_used_mb() <= 6000.0
    config = read_config(settings.config_path, settings.calibration_path)
    routing = config.llm_routing
    deep_model_by_provider = {routing.deep_review.provider: routing.deep_review.model} if routing.deep_review.provider else {}
    providers = [(p.name, deep_model_by_provider.get(p.name, routing.default.model), p.is_local) for p in routing.providers]

    papers = load_review_papers(settings, limit=1)
    paper = papers[0] if papers else ("Sample", "Reproducibility in machine learning is the ability to obtain consistent results. " * 60)
    feed_prompt = _sample_feed_prompt(config)

    def run(stage: str, provider_name: str, model: str) -> tuple[int, int, float] | None:
        provider = routing.provider_by_name(provider_name)
        if provider.is_local and not (include_local and local_memory_ok):
            return None  # remote-only by default; opted-in local still gated on swap headroom
        llm = build_client_for_provider(provider, model, enable_thinking=(stage == "deep_review" and provider.thinking_on))
        start = perf_counter()
        if stage == "feed":
            out = to_text(llm.prompt(feed_prompt))
            return len(feed_prompt), len(out), perf_counter() - start
        digest = quality_review.assess_digest(title=paper[0], full_text=paper[1], config=config, llm=llm,
                                               max_chars=config.quality_review.max_text_chars)
        secs = perf_counter() - start
        return len(paper[1][:config.quality_review.max_text_chars]), len(str(digest.model_dump())), secs

    costs = measure_stage_costs(providers, run=run)
    summary = summarize_costs(costs)
    recommendation = recommend_profile(costs)
    upsert_calibration_entries(settings.calibration_path, {}, meta_key="stage_costs",
                               meta={"rows": summary["rows"], "heaviest_by_tokens": summary["heaviest_by_tokens"],
                                     "heaviest_by_secs": summary["heaviest_by_secs"], "recommendation": recommendation})
    return {"summary": summary, "recommendation": recommendation, "providers": [p[0] for p in providers]}


def _sample_feed_prompt(config: GoalsConfig) -> str:
    """A representative feed-triage prompt (real DEFAULT_TRIAGE_PROMPT shape) for cost timing."""
    from zotero_summarizer.services.triage.prompts import DEFAULT_TRIAGE_PROMPT

    return DEFAULT_TRIAGE_PROMPT.format(
        research_goals="\n".join(f"- {g}" for g in config.research_goals),
        triage_criteria="\n".join(f"- {c}" for c in config.triage_criteria),
        relevance_scale="\n".join(f"{k}: {v}" for k, v in sorted(config.relevance_scale.items())),
        reading_priority_scale="\n".join(f"{k}: {v}" for k, v in config.reading_priority_scale.items()),
        title="A sample paper on multi-agent reasoning for clinical decision support",
        doi="10.0000/sample", summary="This paper proposes a multi-agent framework with ablations on two benchmarks. " * 8,
        corpus_context="(no prior corpus context)", output_language=config.output_language,
    )


@dataclass(frozen=True)
class StageCost:
    stage: str
    provider: str
    model: str
    is_local: bool
    approx_prompt_tokens: int
    approx_completion_tokens: int
    secs: float

    @property
    def approx_total_tokens(self) -> int:
        return self.approx_prompt_tokens + self.approx_completion_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "provider": self.provider, "model": self.model, "is_local": self.is_local,
            "approx_prompt_tokens": self.approx_prompt_tokens,
            "approx_completion_tokens": self.approx_completion_tokens,
            "approx_total_tokens": self.approx_total_tokens, "secs": round(self.secs, 1),
        }


def measure_stage_costs(
    providers: list[tuple[str, str, bool]],
    *,
    run: Callable[[str, str, str], tuple[int, int, float] | None],
) -> list[StageCost]:
    """For each (provider_name, model, is_local) and each measured stage, run the stage's
    real prompt once and record approx tokens (chars/4) + secs. ``run(stage, provider,
    model) -> (prompt_chars, completion_chars, secs)`` is injected so the LLM call stays at
    the edge; it may return ``None`` to SKIP a (stage, provider) the caller chose not to
    run (e.g. a local deep-review on a memory-constrained box) — skipped pairs are omitted."""
    costs: list[StageCost] = []
    for provider_name, model, is_local in providers:
        for stage in _MEASURED_STAGES:
            measured = run(stage, provider_name, model)
            if measured is None:
                continue
            prompt_chars, completion_chars, secs = measured
            costs.append(StageCost(
                stage=stage, provider=provider_name, model=model, is_local=is_local,
                approx_prompt_tokens=int(prompt_chars / _CHARS_PER_TOKEN),
                approx_completion_tokens=int(completion_chars / _CHARS_PER_TOKEN),
                secs=secs,
            ))
    return costs


def summarize_costs(costs: list[StageCost]) -> dict[str, Any]:
    """Pure aggregation: the heaviest stage by tokens and by latency, plus per-stage
    per-provider rows. Drives the 'which steps are heavy' UI copy."""
    if not costs:
        raise ValueError("summarize_costs needs at least one StageCost")
    by_tokens = max(costs, key=lambda c: c.approx_total_tokens)
    by_secs = max(costs, key=lambda c: c.secs)
    return {
        "heaviest_by_tokens": {"stage": by_tokens.stage, "approx_total_tokens": by_tokens.approx_total_tokens},
        "heaviest_by_secs": {"stage": by_secs.stage, "secs": round(by_secs.secs, 1)},
        "rows": [c.as_dict() for c in costs],
    }


def recommend_profile(costs: list[StageCost]) -> dict[str, Any]:
    """Heuristic: deep_review is the heavy stage. If a REMOTE provider can run it (and runs
    it faster than the local one, or the local one wasn't measured/can't), recommend
    'hybrid' — offload the heavy stage, keep the cheap stages local. Else 'local'. Pure +
    testable; 'rationale' is shown to the user."""
    if not costs:
        raise ValueError("recommend_profile needs at least one StageCost")
    deep = [c for c in costs if c.stage == "deep_review"]
    if not deep:
        return {"profile": "local", "rationale": "deep_review not measured; defaulting to fully local."}
    remote_deep = [c for c in deep if not c.is_local]
    local_deep = [c for c in deep if c.is_local]
    if remote_deep:
        best_remote = min(remote_deep, key=lambda c: c.secs)
        if not local_deep:
            return {"profile": "hybrid", "rationale": (
                f"deep_review is the heavy stage and the remote '{best_remote.provider}' runs it "
                f"in {best_remote.secs:.0f}s (local deep review not measured / memory-constrained) — "
                "route the deep review remote, keep feed/backlog local.")}
        slowest_local = max(local_deep, key=lambda c: c.secs)
        if best_remote.secs < slowest_local.secs:
            return {"profile": "hybrid", "rationale": (
                f"deep_review is heavy: remote '{best_remote.provider}' {best_remote.secs:.0f}s vs "
                f"local {slowest_local.secs:.0f}s — route the deep review remote, keep feed/backlog local.")}
    return {"profile": "local", "rationale": "local runs deep_review competitively; keep everything local."}
