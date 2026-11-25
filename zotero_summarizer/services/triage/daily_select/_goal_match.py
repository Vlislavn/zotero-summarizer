"""``aux_context.goal_sims`` (``{goal text: cosine}``): the one parser, and which goal matched best.

``row_goal_sim`` keeps the max value; ``best_goal_text`` keeps its key so the Today card can
say WHICH goal (display only, never feeds ranking). ``None`` = no goal signal.
"""
from __future__ import annotations

from typing import Any


def parse_goal_sims(aux: dict[str, Any]) -> dict[Any, float]:
    """``aux["goal_sims"]`` as ``{goal: cosine}`` without null values (``{}`` if absent/null)."""
    sims = aux.get("goal_sims")
    if sims is None:
        return {}
    if not isinstance(sims, dict):
        raise ValueError("payload.aux_context.goal_sims must be a JSON object or null")
    return {goal: float(v) for goal, v in sims.items() if v is not None}


def best_goal_text(sims: dict[Any, float]) -> str | None:
    """Goal text with the highest cosine (``None`` if none)."""
    return str(max(sims, key=sims.__getitem__)) if sims else None
