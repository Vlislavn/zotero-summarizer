"""Shared ORDER-time score blend: relevance × goal-text similarity × prestige.

The single computational definition of the validated re-rank used by BOTH the
Library reading queue (``services/library/_ranking``) and the Today slate
(``services/triage/daily_select``). Pure cohort math — no I/O, no app state —
so each consumer keeps its own record shape, tie-breakers, and signal adapters.

Provenance of the weights (measured, not guessed):

* ``GOAL_BLEND_WEIGHT = 0.4`` — blind-judge Library benchmark: ``0.6·gate +
  0.4·goal`` lifts NDCG@10 0.38→0.72 and P@10 50%→100% (goal_sim
  Spearman-vs-judgment 0.72 vs the gate's 0.40). The gate alone over-weights
  "similar to what I've saved"; the goal term adds "similar to what I said I
  want".
* ``PRESTIGE_BLEND_WEIGHT = 0.15`` — kept below the goal weight (which
  Spearman-dominates); floats high-quality work up *within* the
  relevance×goal order without ever penalising missing evidence.

Blend contract (mirrors the measured Library behaviour exactly):

* Each signal is min-max normalized over the scored cohort. A degenerate range
  (several identical present values, or a single-row cohort) → 0.5 for all: the
  signal can't separate them, so it's uninformative. EXCEPTION — a *lone*
  present, positive ``goal_sim`` (the cohort's ONLY goal evidence) → 1.0, so it
  tops the goal axis over the no-evidence rows pinned at 0.0. "One present
  value" (sole evidence) is deliberately distinct from "many identical values"
  (uninformative). Prestige needs no such rule: its absent rows take the median,
  so a lone known value leaves every row equal and order is unaffected anyway.
* A signal absent from the WHOLE cohort folds its weight back into relevance
  (goals+prestige → 0.45/0.40/0.15; goals only → 0.60/0.40; neither → pure
  relevance).
* A row missing goal_sim while others have it scores 0.0 on that term (no
  evidence of goal match ranks below evidenced matches).
* A row missing prestige while others have it scores the MEDIAN of the known
  set ("typical quality") — cold-start / uncited work is never demoted.
"""
from __future__ import annotations

GOAL_BLEND_WEIGHT = 0.4
PRESTIGE_BLEND_WEIGHT = 0.15


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    return s[len(s) // 2]


def _norm(v: float, lo: float, hi: float, degenerate: float = 0.5) -> float:
    # ``degenerate`` is the fill for a zero-width range (lo == hi): 0.5 when the
    # tied values are uninformative, 1.0 for a lone sole-evidence value (caller
    # decides — see ``blend_scores``).
    return (v - lo) / (hi - lo) if hi > lo else degenerate


def blend_scores(
    relevance: list[float],
    goal_sim: list[float | None],
    prestige: list[float | None],
    *,
    goal_weight: float = GOAL_BLEND_WEIGHT,
    prestige_weight: float = PRESTIGE_BLEND_WEIGHT,
) -> list[float]:
    """Blend key per row (higher = better) for one scored cohort.

    ``relevance``, ``goal_sim`` and ``prestige`` are parallel lists; ``None``
    means "signal unavailable for this row". Prestige values must be KNOWN
    evidence only (e.g. an OpenAlex citation percentile) — never a fallback
    derived from relevance itself, which would be circular. Scale of each
    list is irrelevant (min-max over the cohort); only order within the
    cohort matters.
    """
    n = len(relevance)
    if len(goal_sim) != n or len(prestige) != n:
        raise ValueError(
            f"parallel lists required: relevance={n}, goal_sim={len(goal_sim)}, "
            f"prestige={len(prestige)}"
        )
    if n == 0:
        return []

    rels = [float(r) for r in relevance]
    gss = [float(g) for g in goal_sim if g is not None]
    prs = [float(p) for p in prestige if p is not None]
    rlo, rhi = min(rels), max(rels)
    glo, ghi = (min(gss), max(gss)) if gss else (0.0, 1.0)
    plo, phi = (min(prs), max(prs)) if prs else (0.0, 1.0)
    pmed = _median(prs) if prs else None

    w_goal = goal_weight if gss else 0.0
    w_prest = prestige_weight if pmed is not None else 0.0
    w_rel = 1.0 - w_goal - w_prest

    # Sole-evidence fill for a degenerate goal range: when exactly ONE row in a
    # multi-row cohort carries a positive goal_sim, it is the only goal evidence
    # and must top the goal axis (1.0), clearly above the no-evidence rows pinned
    # at 0.0 — NOT the neutral 0.5 used when several identical present values
    # can't be separated. (A single-row cohort, n == 1, has no rows to rank
    # against, so it stays uninformative.)
    goal_degen = 1.0 if (len(gss) == 1 and n > 1 and gss[0] > 0) else 0.5

    out: list[float] = []
    for i in range(n):
        rn = _norm(rels[i], rlo, rhi)
        gn = _norm(float(goal_sim[i]), glo, ghi, goal_degen) if goal_sim[i] is not None else 0.0
        pv = prestige[i]
        pn = _norm(float(pv) if pv is not None else pmed, plo, phi) if pmed is not None else 0.0
        out.append(w_rel * rn + w_goal * gn + w_prest * pn)
    return out


__all__ = ["GOAL_BLEND_WEIGHT", "PRESTIGE_BLEND_WEIGHT", "blend_scores"]
