"""Frozen result types for :mod:`services.daily_select`.

Kept tiny and dependency-free so the API-route subagent can import them
without pulling in sqlite or the rest of the service layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SlatePaper:
    """One card in the daily slate."""

    item_key: str
    item_id: int
    title: str
    authors: str
    venue: str
    role: str
    composite_score: float
    surprise_score: float
    corpus_affinity: float
    prestige_score: float
    rationale: str
    shap_top: list[dict[str, Any]]
    decision: str


@dataclass(frozen=True)
class DailySlate:
    """Result of :func:`assemble_daily_slate`."""

    papers: list[SlatePaper]
    pool_size: int
    capped_at: int
    lookback_hours: int
    empty_role_events: list[str] = field(default_factory=list)


__all__ = ["SlatePaper", "DailySlate"]
