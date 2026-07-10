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

from zotero_summarizer.domain import ReadingPriority

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


# --- Order-time deep-review QUALITY lift -------------------------------------
# A SMALL, capped bonus the CONSUMER adds to the blend key (this module only
# computes it — stays pure). It reorders WITHIN a relevance band: banding is
# derived independently from the raw 1-5 relevance score, never from this sort
# key, so the bonus cannot move a paper across a band at ANY magnitude. The cap
# only bounds the within-band reorder REACH.
#
# Two modes, the consumer selects via ``use_band``:
#   * grade-only (DEFAULT — the shipped, measured behaviour): the A-D referee grade.
#   * band-primary (a Phase-2-MEASURED arm, not flipped on ahead of its gate): the
#     3-band quality verdict drives it (highlight ↑, flag ↓) with the grade as a
#     small secondary nudge. ``neutral`` AND ``uncertain`` resolve to EXACTLY 0.0
#     (never negative) — ``uncertain`` is a self-consistency / human-look state,
#     not a quality demotion, so it must never bury a borderline (e.g. clinical)
#     paper.
# PROVISIONAL magnitudes: the GOAL/PRESTIGE blend weights above are blind-judge
# measured (NDCG 0.38→0.72), but these bonus magnitudes are hand-set, capped, and
# NOT yet isolated by an eval. `tools/eval_slate_blend.py` measures their effect on
# the slate's own kept/trashed labels (grade-only vs band-primary vs normalized) — it
# has not been run on the current slate (Phase 5 closes this).
DEFAULT_QUALITY_BONUS: dict[str, float] = {"A": 0.06, "B": 0.03, "C": 0.0, "D": -0.02}
DEFAULT_QUALITY_BAND_BONUS: dict[str, float] = {
    "highlight": 0.06, "flag": -0.06, "neutral": 0.0, "uncertain": 0.0,
}


def quality_bonus(
    band: str | None,
    grade: str | None,
    *,
    use_band: bool = False,
    grade_table: dict[str, float] = DEFAULT_QUALITY_BONUS,
    band_table: dict[str, float] = DEFAULT_QUALITY_BAND_BONUS,
    grade_nudge: float = 0.5,
) -> float:
    """Capped deep-review quality lift for one row (pure; the consumer adds it).

    ``use_band=False`` → grade-only (``grade_table[grade]``, default 0.0 when
    ungraded). ``use_band=True`` → band-primary: ``band_table[band]`` plus
    ``grade_nudge`` × the grade lift, EXCEPT a band whose base is 0.0
    (``neutral``/``uncertain``/unknown) returns exactly 0.0 so a borderline paper
    is never demoted by its grade.
    """
    g = str(grade or "").upper()
    if not use_band:
        return grade_table.get(g, 0.0)
    base = band_table.get(str(band or "").lower(), 0.0)
    if base == 0.0:
        return 0.0
    return base + grade_nudge * grade_table.get(g, 0.0)


# Default thresholds for promote_band. Set by tools/eval_quality_promote.py on 68
# firewalled user-verdict rows: goal_sim 0.55 + relevance_floor 3.0 promote 3 must +
# 9 should at 1.00 precision with ZERO flooding; the old 3.5 (should-band boundary)
# promoted just 1 under gate compression, and below 3.0 precision decays. Runtime uses
# the config values (quality_promote_*); these are the fallback when called bare.
DEFAULT_PROMOTE_GOAL_SIM = 0.55
DEFAULT_PROMOTE_RELEVANCE_FLOOR = 3.0
_PROMOTE_GRADES = {"A", "B"}


def promote_band(
    raw_priority: str | None,
    *,
    grade: str | None,
    relevance_score: float | None,
    goal_sim: float | None,
    goal_sim_floor: float = DEFAULT_PROMOTE_GOAL_SIM,
    relevance_floor: float = DEFAULT_PROMOTE_RELEVANCE_FLOOR,
) -> str:
    """Quality → must_read band promotion (the inverse of the auto_quality hide gate).

    The gate's regressor scores are compressed toward the mean → the must_read band
    (≥4.5) is almost never reached on its own (0/69 must recall), so ``raw_priority``
    for a genuinely great paper lands at ``should_read``/``could_read``. This lifts a
    paper to ``must_read`` only when quality AND relevance AGREE — a strict multi-signal
    AND (precision-first: "I can't read much trash"):

      must_read  IF  grade ∈ {A,B}  AND  goal_sim ≥ floor  AND  relevance ≥ floor
      should_read IF grade ∈ {A,B}  AND  relevance ≥ floor   (quality lifts could→should)
      else the raw gate band unchanged.

    A ``None`` signal (no review / unscored) means "not assessed" → no promotion
    (mirrors the no-hide-on-absent-evidence contract, ADR-B2). ``relevance_score``
    here is the GATE's raw 1-5 score (the same field the band derives from); the
    floor is the should_read boundary, so promotion only crosses ONE band up — never
    invents must_read from a dont_read. Pure; the consumer (``_ranking``) applies it
    to the record's ``reading_priority``.
    """
    g = str(grade or "").upper()
    if g not in _PROMOTE_GRADES:
        return raw_priority or ""
    if relevance_score is None or goal_sim is None:
        return raw_priority or ""
    rel = float(relevance_score)
    gs = float(goal_sim)
    if rel < relevance_floor:
        return raw_priority or ""
    # relevance ≥ should-band AND quality A/B → at least should_read; must_read
    # additionally requires strong goal alignment.
    if gs >= goal_sim_floor:
        return ReadingPriority.MUST_READ.value
    return ReadingPriority.SHOULD_READ.value


__all__ = [
    "GOAL_BLEND_WEIGHT",
    "PRESTIGE_BLEND_WEIGHT",
    "DEFAULT_QUALITY_BONUS",
    "DEFAULT_QUALITY_BAND_BONUS",
    "DEFAULT_PROMOTE_GOAL_SIM",
    "DEFAULT_PROMOTE_RELEVANCE_FLOOR",
    "blend_scores",
    "quality_bonus",
    "promote_band",
]
