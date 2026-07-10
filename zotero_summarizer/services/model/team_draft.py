"""Team-draft interleaving of two ranked lists (P3 online A/B, ADR-A9/GAP-G11).

The blind decision instrument for the quality-first flip: given the SAME
candidate pool ranked by two arms (A = the shipped control blend, B = the
quality-first key), merge their ordered top-K lists into ONE slate the user
sees, remembering which arm drafted each slot. The user's normal verdicts on
the ~1-2 slots/day where the arms DISAGREE become paired preference signals
(scored by ``tools/eval_interleave.py``'s SPRT) — 10-100x more sample-efficient
than day-split A/B and immune to day-to-day topic drift, because both arms
compete inside the same day's pool (Radlinski/Chapelle team-draft, with the
shared-pick collapsing of Airbnb/Netflix "competitive pairs": an item BOTH
arms would pick next is credited to neither — it carries no preference
information, and at ~97% expected arm overlap collapsing it is what makes the
signal usable at all).

Mechanics per draft round: if both arms' next available pick is the SAME item,
it enters once as ``team="both"`` (no pair). Otherwise a seeded coin picks who
drafts first; each arm drafts its highest-ranked still-available item, and the
two picks share a ``pair_id`` — one competitive pair. The seed is the slate
date, so the merge is DETERMINISTIC within a day (repeated GETs re-produce the
same slate) while the first-drafter advantage still averages out across days.

Pure list math: no I/O, no app state, items are opaque hashables (the slate
passes row ids). ``k`` truncation never emits a half pair — a pair needs both
sides shown to be a comparison.
"""
from __future__ import annotations

import random
from collections.abc import Hashable, Sequence
from typing import NamedTuple

TEAM_A = "a"
TEAM_B = "b"
TEAM_BOTH = "both"


class DraftPick(NamedTuple):
    """One merged slot: the item, the arm that drafted it, and the competitive
    pair it belongs to (``None`` for shared/uncontested picks)."""

    item: Hashable
    team: str
    pair_id: int | None


def _next_available(items: Sequence[Hashable], start: int, taken: set[Hashable]) -> int:
    """Index of the first item at/after ``start`` not yet merged (== len when exhausted)."""
    i = start
    while i < len(items) and items[i] in taken:
        i += 1
    return i


def team_draft_merge(
    a: Sequence[Hashable],
    b: Sequence[Hashable],
    k: int,
    *,
    seed: str,
) -> list[DraftPick]:
    """Merge ranked lists ``a`` and ``b`` into at most ``k`` picks (see module doc).

    Duplicate items WITHIN one list are an input error (rank lists are sets in
    order) — fail fast rather than silently double-draft.
    """
    if k <= 0:
        raise ValueError(f"k must be positive; got {k}")
    if len(set(a)) != len(a) or len(set(b)) != len(b):
        raise ValueError("team_draft_merge: input lists must not contain duplicates")
    rng = random.Random(seed)
    taken: set[Hashable] = set()
    out: list[DraftPick] = []
    ia = ib = 0
    pair_id = 0
    while len(out) < k:
        ia = _next_available(a, ia, taken)
        ib = _next_available(b, ib, taken)
        na = a[ia] if ia < len(a) else None
        nb = b[ib] if ib < len(b) else None
        if na is None and nb is None:
            break
        if na is not None and na == nb:
            out.append(DraftPick(na, TEAM_BOTH, None))
            taken.add(na)
            continue
        if na is None or nb is None:
            # One arm exhausted — remaining picks are uncontested (no pair).
            item, team = (nb, TEAM_B) if na is None else (na, TEAM_A)
            out.append(DraftPick(item, team, None))
            taken.add(item)
            continue
        # Competitive round: coin for draft order; both picks share a pair_id.
        first_is_a = rng.random() < 0.5
        if k - len(out) == 1:
            # No room for the partner — an unpaired half-round is not a comparison.
            item, team = (na, TEAM_A) if first_is_a else (nb, TEAM_B)
            out.append(DraftPick(item, team, None))
            taken.add(item)
            break
        pair_id += 1
        first = (na, TEAM_A, a) if first_is_a else (nb, TEAM_B, b)
        second_team, second_list = (TEAM_B, b) if first_is_a else (TEAM_A, a)
        out.append(DraftPick(first[0], first[1], pair_id))
        taken.add(first[0])
        # The second arm re-resolves its top available AFTER the first draft.
        # Its head is still available by construction (na != nb here, and only
        # first[0] was just taken) — an IndexError below would mean the round
        # invariant broke, and loud is right.
        js = _next_available(second_list, ib if first_is_a else ia, taken)
        out.append(DraftPick(second_list[js], second_team, pair_id))
        taken.add(second_list[js])
    return out


__all__ = ["DraftPick", "team_draft_merge", "TEAM_A", "TEAM_B", "TEAM_BOTH"]


def _demo() -> None:
    """Runnable check: determinism, no dupes, shared-collapse, pairing, k-cap."""
    a = [1, 2, 3, 4, 5]
    b = [1, 9, 2, 8, 7]
    m1 = team_draft_merge(a, b, 5, seed="2026-07-08")
    m2 = team_draft_merge(a, b, 5, seed="2026-07-08")
    assert m1 == m2, "same seed must reproduce the same merge"
    items = [p.item for p in m1]
    assert len(items) == len(set(items)) == 5, m1
    assert m1[0] == DraftPick(1, TEAM_BOTH, None), m1[0]  # shared top pick, no pair
    # Every competitive pair has exactly two members, one per team.
    by_pair: dict[int, list[DraftPick]] = {}
    for p in m1:
        if p.pair_id is not None:
            by_pair.setdefault(p.pair_id, []).append(p)
    assert by_pair and all(
        len(v) == 2 and {v[0].team, v[1].team} == {TEAM_A, TEAM_B} for v in by_pair.values()
    ), by_pair
    # k=2: shared pick + one truncated (unpaired) competitive slot.
    m3 = team_draft_merge(a, b, 2, seed="x")
    assert len(m3) == 2 and m3[1].pair_id is None, m3
    # Identical lists collapse entirely to 'both'.
    m4 = team_draft_merge(a, a, 3, seed="y")
    assert all(p.team == TEAM_BOTH for p in m4), m4
    # One empty list → all uncontested from the other.
    m5 = team_draft_merge([], b, 3, seed="z")
    assert [p.item for p in m5] == [1, 9, 2] and all(p.team == TEAM_B for p in m5), m5
    print("team_draft demo: OK")


if __name__ == "__main__":
    _demo()
