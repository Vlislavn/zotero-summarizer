"""LLM fan-out widths: how many items / sub-calls run at once, by provider locality.

Shared so the daemon, the deep-review job and the ``verify-deep-review`` CLI never drift.
"""
from __future__ import annotations

from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.config_io import read_config


def effective_llm_concurrency(provider: ProviderConfig | None, item_count: int) -> int:
    """Worker count for a per-item LLM fan-out, conditional on the provider.

    A **local** provider (loopback ``base_url``) runs **serial** (1) so one big
    on-device model isn't asked to serve concurrent inference and thrash host
    RAM. A **remote** provider uses the configured ``TRIAGE_JOB_CONCURRENCY`` cap,
    never more than the work on hand. ``provider=None`` falls to the remote
    branch — the safe default never silently serialises a real remote run.
    """
    n = max(1, int(item_count))
    if provider is not None and provider.is_local:
        return 1
    return max(1, min(settings().triage_job_concurrency, n))


def _quality_knob(name: str) -> int:
    """``quality_review.<name>`` from the config (0 = unset → provider cap)."""
    try:
        return int(getattr(read_config(settings().config_path, settings().calibration_path).quality_review, name, 0) or 0)
    except FileNotFoundError:  # no goals.yaml yet → provider caps
        return 0


def deep_review_fleet_concurrency(provider: ProviderConfig | None, item_count: int) -> int:
    """Worker count for the MULTI-paper deep-review fan-out (the N "Read next" picks the
    fleet / prewarm review at once).

    A **local** provider stays **serial** (1) — one on-device model can't serve
    concurrent inference without thrashing host RAM. A **remote** provider fans out to
    the work on hand, capped by its own ``max_sub_concurrency`` (the per-provider remote
    concurrency budget) when set, else **all N at once** (an API has no host-RAM
    constraint). Deliberately NOT the global ``TRIAGE_JOB_CONCURRENCY`` — that knob
    serialises LOCAL triage for RAM safety, so it must never throttle a genuinely remote,
    RAM-free batch (the bug: with ``TRIAGE_JOB_CONCURRENCY=1`` a 5-pick remote batch ran
    serially). ``provider=None`` falls to the remote branch (never silently serialises a
    real remote run). Peak concurrent calls is this × ``deep_review_sub_concurrency``: that
    many calls in flight PER REVIEW, enforced in ``_review_attempt`` however the branches
    and inner pools fan out; both are bounded by ``max_sub_concurrency`` when capped."""
    n = max(1, int(item_count))
    if provider is not None and provider.is_local:
        return 1
    cap = _quality_knob("fleet_papers") or (getattr(provider, "max_sub_concurrency", None) if provider else None)
    return min(n, cap) if cap else n


def deep_review_sub_concurrency(provider: ProviderConfig | None) -> int:
    """Number of LLM SUB-calls (self-consistency rubric samples / per-goal
    summaries) to run in parallel WITHIN one paper's deep review.

    A **local** provider stays serial (1) — one big on-device model can't absorb
    concurrent inference without thrashing host RAM. A **remote** provider uses its
    own ``max_sub_concurrency`` cap, falling back to ``TRIAGE_JOB_CONCURRENCY`` when
    unset. ``provider=None`` falls to the remote branch (never silently serialises a
    real remote run). Shared by the background job and the ``verify-deep-review`` CLI
    so the two never drift."""
    if provider is not None and provider.is_local:
        return 1
    configured = _quality_knob("calls_per_paper") or (getattr(provider, "max_sub_concurrency", None) if provider else None)
    if configured is not None:
        return max(1, int(configured))
    return max(1, settings().triage_job_concurrency)
