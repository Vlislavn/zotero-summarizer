"""Quality-FIRST order key: quality leads, topicality soft-gates.

The SOTA re-rank for the user's directive — *"a high-quality paper a little bit
off-topic beats a poor-quality paper on-topic."* Kept as a SEPARATE module from
``rank_blend`` (the shipped relevance×goal×prestige blend, unchanged as the
control arm) so the two are byte-independent and A/B-able behind one flag.

Pattern (card PAT-DualOptimization_jobrec-001): de-conflate *qualification*
(quality) from *preference* (topicality). Quality LEADS the key; topicality is a
FLOORED multiplicative gate (never a hard drop, never a small additive term the
topic axis swamps — that was the shipped ±0.06 failure). The key:

    key_i = q_i · (0.5 + 0.5·t_i)

* ``q`` — unified quality ∈ [0,1], the LEAD. Grade-primary when the paper was
  deep-reviewed (A=1.0 / B=0.75 / C=0.4 / D=0.0), ``flag`` band caps it ≤ 0.25.
  Unreviewed (the ~71% with only an abstract): a dims prior from the LLM's
  rigor+evidence, SHRUNK to [0.25, 0.75] so an abstract-only guess never
  out-ranks a reviewed A nor sinks below the flag cap; no dims → 0.5
  (not-assessed = typical). ``q`` is NOT cohort-normed — the A..D anchors are
  absolute, so a whole-cohort of C-papers stays mid, not stretched to fill [0,1].
* ``t`` — topicality ∈ [0,1]: ``0.6·norm(goal_sim) + 0.4·(goal_alignment−1)/4``,
  per-row renormalized over whichever topical signals are present. goal_sim is
  cohort min-maxed (the ranking lever, blind-judge Spearman 0.72); goal_alignment
  is the LLM's 1-5 dim, absolute. Neither present → t=0.5 (neutral).
* gate ``0.5 + 0.5·t`` ∈ [0.5, 1.0]: even a fully off-topic paper keeps HALF its
  quality — the "soft" in soft-gate. This is why relevance_score is deliberately
  ABSENT from the lead: de-leaked (2026-07-07, kept=42 firewall) its AUC is 0.634,
  the WEAKEST of the three signals, NOT the 0.852 head an earlier leaked eval
  implied. corpus_affinity (the inverted 0.268 axis) and prestige (orthogonal to
  quality, ρ≈−0.25) are out entirely.

Provable invariant (pinned by ``__main__`` + ``tests/test_rank_blend_quality.py``):
a grade-A off-topic paper always out-ranks a grade-D on-topic one —
``1·(0.5+0) = 0.5 > 0·(0.5+0.5·1) = 0`` — because a D paper's ``q=0`` zeroes the
key regardless of topicality. That inequality IS the user's directive as an
assertion.

Pure cohort math: no I/O, no app state. The consumer (``daily_select._candidate``)
adapts its records to the parallel lists.
"""
from __future__ import annotations

# Absolute grade anchors — NOT cohort-normed (keeps A..D semantics stable across
# weak/strong feed weeks). D=0.0 makes any D paper's key exactly 0 (sinks below
# every non-D), which is the directive's "poor quality sinks" made literal.
GRADE_QUALITY: dict[str, float] = {"A": 1.0, "B": 0.75, "C": 0.4, "D": 0.0}
# The ``flag`` band (weakest quality verdict) caps q so a flagged reviewed paper
# can never lead, whatever its letter grade.
FLAG_QUALITY_CAP = 0.25
# Abstract-only prior band: an unreviewed guess lives in [0.25, 0.75] — never
# above a reviewed A (1.0), never below the flag cap (0.25).
UNREVIEWED_PRIOR_LO = 0.25
UNREVIEWED_PRIOR_HI = 0.75
UNREVIEWED_DEFAULT = 0.5  # no dims either → not-assessed = typical
# Topicality mix: goal_sim (cohort-normed ranking lever) over the LLM dim.
TOPIC_GOAL_SIM_WEIGHT = 0.6
GATE_FLOOR = 0.5  # off-topic keeps half its quality (the soft in soft-gate)


def _dim01(v: float | None) -> float | None:
    """Map a 1-5 LLM dimension onto [0,1] (``(v−1)/4``), clamped. ``None`` passes."""
    if v is None:
        return None
    return min(1.0, max(0.0, (float(v) - 1.0) / 4.0))


def unified_quality(
    grade: str | None,
    band: str | None,
    *,
    rigor: float | None = None,
    evidence: float | None = None,
) -> float:
    """Per-row unified quality ∈ [0,1] — the LEAD term (NO cohort dependency).

    Reviewed (grade ∈ A/B/C/D): the absolute grade anchor, then ``flag`` band
    caps it ≤ 0.25. Unreviewed: the mean of the LLM's normalized rigor+evidence
    dims, SHRUNK into [0.25, 0.75]; no dims → 0.5. ``neutral``/``uncertain`` bands
    never demote (only ``flag`` caps) — an ``uncertain`` verdict is a
    human-look/self-consistency state, not a quality penalty.
    """
    g = str(grade or "").upper()
    if g in GRADE_QUALITY:
        q = GRADE_QUALITY[g]
        if str(band or "").lower() == "flag":
            q = min(q, FLAG_QUALITY_CAP)
        return q
    # Unreviewed — abstract-only prior from rigor+evidence, shrunk to the band.
    dims = [d for d in (_dim01(rigor), _dim01(evidence)) if d is not None]
    if not dims:
        return UNREVIEWED_DEFAULT
    prior = sum(dims) / len(dims)
    return UNREVIEWED_PRIOR_LO + (UNREVIEWED_PRIOR_HI - UNREVIEWED_PRIOR_LO) * prior


def _norm(v: float, lo: float, hi: float) -> float:
    return (v - lo) / (hi - lo) if hi > lo else 0.5


def _topicality(
    goal_sim: float | None,
    goal_alignment: float | None,
    glo: float,
    ghi: float,
) -> float:
    """Row topicality ∈ [0,1]: weighted mean of whichever topical signals are
    present (goal_sim cohort-normed @ 0.6, goal_alignment absolute @ 0.4),
    renormalized per-row so a row with only one signal isn't capped. Neither
    present → 0.5 (neutral — the soft gate then leaves quality to rank)."""
    parts: list[tuple[float, float]] = []  # (value, weight)
    if goal_sim is not None:
        parts.append((_norm(float(goal_sim), glo, ghi), TOPIC_GOAL_SIM_WEIGHT))
    ga = _dim01(goal_alignment)
    if ga is not None:
        parts.append((ga, 1.0 - TOPIC_GOAL_SIM_WEIGHT))
    if not parts:
        return 0.5
    return sum(v * w for v, w in parts) / sum(w for _, w in parts)


def quality_first_key(
    qualities: list[float],
    goal_sim: list[float | None],
    goal_alignment: list[float | None],
    *,
    gate_floor: float = GATE_FLOOR,
) -> list[float]:
    """``key_i = q_i · (gate_floor + (1−gate_floor)·t_i)`` for one cohort (higher = better).

    ``qualities`` are per-row unified-quality values (from :func:`unified_quality`,
    already absolute [0,1] — NOT normed here). ``goal_sim``/``goal_alignment`` are
    parallel lists; ``None`` = signal absent for that row. goal_sim is min-maxed
    over the cohort's present values; goal_alignment is used absolute.

    ``gate_floor`` ∈ [0,1] is the fraction of quality an off-topic paper keeps
    (the "soft" in soft-gate). Defaults to the shipped ``GATE_FLOOR`` (0.5); the
    Track-C frontier eval sweeps it (a lower floor lets topicality bite harder, a
    higher floor pushes toward pure quality). Not a runtime knob yet — the shipped
    slate always calls with the default; only ``tools/eval_slate_blend.py`` varies it.
    """
    n = len(qualities)
    if len(goal_sim) != n or len(goal_alignment) != n:
        raise ValueError(
            f"parallel lists required: qualities={n}, goal_sim={len(goal_sim)}, "
            f"goal_alignment={len(goal_alignment)}"
        )
    gss = [float(g) for g in goal_sim if g is not None]
    glo, ghi = (min(gss), max(gss)) if gss else (0.0, 1.0)
    out: list[float] = []
    for i in range(n):
        t = _topicality(goal_sim[i], goal_alignment[i], glo, ghi)
        out.append(qualities[i] * (gate_floor + (1.0 - gate_floor) * t))
    return out


__all__ = [
    "GRADE_QUALITY",
    "FLAG_QUALITY_CAP",
    "unified_quality",
    "quality_first_key",
]


def _demo() -> None:
    """Runnable check: the directive invariant + the anchor/prior/gate contracts."""
    # The pinned invariant — grade-A off-topic beats grade-D on-topic, always.
    qa = unified_quality("A", None)  # 1.0
    qd = unified_quality("D", None)  # 0.0
    keys = quality_first_key([qa, qd], [0.0, 1.0], [1.0, 5.0])
    assert keys[0] > keys[1], keys
    assert abs(keys[0] - 0.5) < 1e-9 and abs(keys[1]) < 1e-9, keys

    # Grade anchors + flag cap.
    assert unified_quality("B", "neutral") == 0.75
    assert unified_quality("C", None) == 0.4
    assert unified_quality("C", "flag") == 0.25   # flag caps below the C anchor
    assert unified_quality("A", "flag") == 0.25
    assert unified_quality("B", "uncertain") == 0.75  # uncertain never demotes

    # Unreviewed prior lives strictly inside [0.25, 0.75].
    assert unified_quality(None, None) == 0.5                      # no dims
    assert unified_quality(None, None, rigor=5, evidence=5) == 0.75  # top dims
    assert unified_quality(None, None, rigor=1, evidence=1) == 0.25  # floor dims
    q_mid = unified_quality(None, None, rigor=3, evidence=3)
    assert 0.25 < q_mid < 0.75, q_mid

    # A reviewed C (0.4) can't be out-led by the best abstract-only prior (0.75)?
    # It CAN — the prior tops out at 0.75 > 0.4 by design (an unknown paper with
    # strong-looking rigor is a plausible read); only a reviewed A/B clears it.
    assert unified_quality(None, None, rigor=5, evidence=5) > unified_quality("C", None)

    # Soft gate: off-topic keeps exactly half its quality; on-topic keeps all.
    off = quality_first_key([1.0], [None], [None])   # neutral t=0.5 → gate 0.75
    assert abs(off[0] - 0.75) < 1e-9, off
    # Degenerate cohort (identical goal_sim) → norm 0.5 → order is pure quality.
    deg = quality_first_key([0.75, 0.4], [0.3, 0.3], [None, None])
    assert deg[0] > deg[1], deg
    print("rank_blend_quality demo: OK")


if __name__ == "__main__":
    _demo()
