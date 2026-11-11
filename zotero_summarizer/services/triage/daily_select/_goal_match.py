"""Which configured research goal a candidate matched best (display only).

``aux_context.goal_sims`` is ``{goal text: cosine}``; ``row_goal_sim`` keeps the
max value, this keeps its key so the Today card can say WHICH goal. It never
feeds ranking. ``None`` = no goal signal (same contract as ``goal_sim``).
"""
from __future__ import annotations

from typing import Any


def row_goal_text(payload: dict[str, Any]) -> str | None:
    """Goal text with the highest cosine in ``aux_context.goal_sims`` (``None`` if none)."""
    sims = (payload.get("aux_context") or {}).get("goal_sims")
    scored = {str(g): float(v) for g, v in (sims or {}).items() if v is not None}
    return max(scored, key=scored.__getitem__) if scored else None
