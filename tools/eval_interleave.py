"""P3 interleave scorer: the SPRT that decides the quality-first flip (ADR-A9/GAP-G11).

Joins the ``interleave_log`` arm attributions (written by the Today slate when
``ZS_RANK_INTERLEAVE`` is on) with the USER's engagement signals, scores every
competitive pair (the slots where the A0 control blend and the A2 quality-first
arm disagreed), and runs a Wald SPRT on the win stream:

    H0: P(A2 wins | disagreement) = 0.5   (no real preference — keep A0)
    H1: P(A2 wins | disagreement) = 0.7   (real, actionable preference — flip)
    alpha = beta = 0.10  ->  stop when cumulative LLR crosses +/- ln(9)

Engagement per item (explicit verdict wins over implicit decision):
  * ``label_verdicts`` with ``source='user'`` ONLY — must=3 / should=2 / could=1 /
    dont_read=-1. FIREWALL: ``auto_quality`` verdicts are A2's own quality signal
    and ``machine_add`` is automation — letting either in would score the arm by
    its own output (grader-firewall rule), so any non-user source is excluded.
  * else the row's decision, gated by ``decision_reason``: selected /
    user_approved / black_swan count +2 ONLY when the reason marks a USER action
    (Today "Add", review-UI approve/apply/relabel — ``_USER_DECISION_REASONS``).
    FIREWALL: the daemon's ``run_daily_selection`` writes the SAME decisions with
    zero user involvement (plateau reasons ``elbow``/``*_fallback_to_cap``/
    ``cap_overrode_elbow``, black-swan ``surprise_pick``) — crediting those would
    leak machine behavior (correlated with the A0-ish composite) into the score.
    An unknown reason scores 0: conservative, loses a trial, never biases.
    ``user_rejected`` is user-only by construction -> -1 unconditionally.
Pair outcome = higher engagement wins; equal (incl. both-untouched) = tie,
discarded (no preference information).

I.I.D. GUARD (``score_pairs``): an item that lingers unverdicted re-enters a
NEW pair each day it stays in the pool, so one eventual verdict would replay as
N correlated "trials". Each item's engagement therefore counts ONLY in its LAST
logged competitive pair — the exposure nearest the user's action (verdicts drop
the item from the pool, so no later pair exists) — and contributes 0 to earlier
pairs. Untouched items are 0 everywhere, so their repeats are harmless.

Read-only; writes nothing. Light (no embed, no models). Safe to run any time —
continuous monitoring is the point of a sequential test (no alpha inflation):

    .venv/bin/python tools/eval_interleave.py
"""
from __future__ import annotations

import math
import sys

# --- pure decision math (unit-tested in tests/test_team_draft.py) ---------------

SPRT_P1 = 0.7
SPRT_ALPHA = 0.10
SPRT_BETA = 0.10

_VERDICT_SCORE = {"must_read": 3, "should_read": 2, "could_read": 1, "dont_read": -1}
_KEPT_DECISIONS = ("selected", "user_approved", "black_swan")
_TRASH_DECISION = "user_rejected"
# decision_reason values that mark a USER-initiated keep (see module doc firewall).
# Sources: daily_actions._set_decision / review.materialize_row / review UI writers.
_USER_DECISION_REASONS = (
    "materialized_via_today_add",
    "today_add_zotero_pending",
    "materialized_via_review_apply",
    "user_approved_in_review_ui",
)
_USER_DECISION_REASON_PREFIXES = ("user_relabel:",)


def engagement_score(
    user_priority: str | None, decision: str | None, decision_reason: str | None = None
) -> int:
    """Item engagement on the ladder above. Explicit user verdict wins; kept
    decisions need a user-action reason; absent everything -> 0 (untouched)."""
    if user_priority is not None:
        if user_priority not in _VERDICT_SCORE:
            raise ValueError(f"unknown verdict priority {user_priority!r}")
        return _VERDICT_SCORE[user_priority]
    if decision in _KEPT_DECISIONS:
        reason = decision_reason or ""
        if reason in _USER_DECISION_REASONS or reason.startswith(
            _USER_DECISION_REASON_PREFIXES
        ):
            return 2
        return 0  # machine-authored keep (daemon plateau/surprise) — firewalled out
    if decision == _TRASH_DECISION:
        return -1
    return 0


def pair_outcome(eng_a0: int, eng_a2: int) -> str:
    """'a2' / 'a0' / 'tie' for one competitive pair."""
    if eng_a2 > eng_a0:
        return "a2"
    if eng_a0 > eng_a2:
        return "a0"
    return "tie"


def score_pairs(
    log_rows: list[dict], eng_by_item: dict[int, int]
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Group competitive pairs and score them (pure; see module-doc I.I.D. guard).

    Returns ``(wins, per_day)`` — ``wins`` totals ``a0``/``a2``/``tie``,
    ``per_day[day]`` holds ``pairs``/``a0``/``a2``/``tie``. An item's engagement
    applies only in its LAST logged pair; elsewhere it scores 0. Corrupt logs
    (duplicate team in a pair, a pair without exactly {a0, a2}) fail loud.
    """
    pairs: dict[tuple[str, int], dict[str, dict]] = {}
    last_pair_day: dict[int, str] = {}
    for row in log_rows:
        if row["pair_id"] is None:
            continue
        slot = pairs.setdefault((row["day"], row["pair_id"]), {})
        if row["team"] in slot:
            raise ValueError(
                f"corrupt interleave_log: duplicate team {row['team']!r} in pair "
                f"{(row['day'], row['pair_id'])}"
            )
        slot[row["team"]] = row
        prior = last_pair_day.get(row["item_id"])
        if prior is None or row["day"] > prior:
            last_pair_day[row["item_id"]] = row["day"]

    def _eng(row: dict, day: str) -> int:
        if last_pair_day[row["item_id"]] != day:
            return 0  # engagement counts only at the item's last exposure
        return eng_by_item[row["item_id"]]

    wins = {"a0": 0, "a2": 0, "tie": 0}
    per_day: dict[str, dict[str, int]] = {}
    for (day, pair_id), members in sorted(pairs.items()):
        if set(members) != {"a0", "a2"}:
            raise ValueError(
                f"corrupt interleave_log: pair {(day, pair_id)} has teams {sorted(members)}"
            )
        outcome = pair_outcome(_eng(members["a0"], day), _eng(members["a2"], day))
        wins[outcome] += 1
        day_bucket = per_day.setdefault(day, {"pairs": 0, "a2": 0, "a0": 0, "tie": 0})
        day_bucket["pairs"] += 1
        day_bucket[outcome] += 1
    return wins, per_day


def sprt_state(
    wins_a2: int,
    wins_a0: int,
    *,
    p1: float = SPRT_P1,
    alpha: float = SPRT_ALPHA,
    beta: float = SPRT_BETA,
) -> tuple[float, str]:
    """Cumulative log-likelihood ratio and the SPRT verdict.

    Returns ``(llr, state)`` with state one of ``flip`` (accept H1 — A2 wins),
    ``keep`` (accept H0 — stay on A0), ``continue`` (between boundaries).
    """
    if not 0.5 < p1 < 1.0:
        raise ValueError(f"p1 must be in (0.5, 1); got {p1}")
    llr = wins_a2 * math.log(p1 / 0.5) + wins_a0 * math.log((1.0 - p1) / 0.5)
    upper = math.log((1.0 - beta) / alpha)
    lower = math.log(beta / (1.0 - alpha))
    if llr >= upper:
        return llr, "flip"
    if llr <= lower:
        return llr, "keep"
    return llr, "continue"


# --- scoring against the live DB ------------------------------------------------

def _item_engagement(
    row: dict, mkey_by_id: dict, decisions_by_id: dict, verdicts: dict
) -> int:
    """Engagement for one interleave_log row. Verdicts join via mkey (post-
    materialize) or stable_feed_key (in-place feed verdicts) — the two shapes
    live verdict writers persist; the raw ``item_key`` is a belt-and-suspenders
    third (no writer uses it today, and a false match is impossible)."""
    keys = [
        mkey_by_id.get(row["item_id"]) or "",
        row.get("stable_feed_key") or "",
        row.get("item_key") or "",
    ]
    priority = next((verdicts[k] for k in keys if k and k in verdicts), None)
    decision, reason = decisions_by_id.get(row["item_id"], (None, None))
    return engagement_score(priority, decision, reason)


def main() -> None:
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.storage._repo_labels import list_all_label_verdicts
    from zotero_summarizer.storage.interleave import fetch_interleave_log
    import sqlite3

    settings_ = get_settings()
    db = settings_.triage_db_path
    log_rows = fetch_interleave_log(db)
    if not log_rows:
        raise SystemExit(
            "no interleave_log rows — enable the experiment first (ZS_RANK_INTERLEAVE=1) "
            "and let the Today slate assemble at least once."
        )

    # USER verdicts only (see module doc firewall note).
    verdicts = {
        v["item_key"]: v["user_priority"]
        for v in list_all_label_verdicts(db)
        if v.get("source") == "user"
    }
    ids = sorted({r["item_id"] for r in log_rows})
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(ids))
        fetched = conn.execute(
            f"SELECT id, decision, decision_reason, materialized_zotero_key "
            f"FROM processed_feed_items WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    finally:
        conn.close()
    decisions_by_id = {r["id"]: (r["decision"], r["decision_reason"]) for r in fetched}
    mkey_by_id = {r["id"]: r["materialized_zotero_key"] for r in fetched}

    eng_by_item = {
        r["item_id"]: _item_engagement(r, mkey_by_id, decisions_by_id, verdicts)
        for r in log_rows
    }
    try:
        wins, per_day = score_pairs(log_rows, eng_by_item)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    n_days = len({r["day"] for r in log_rows})
    n_both = sum(1 for r in log_rows if r["team"] == "both")
    n_pairs = sum(b["pairs"] for b in per_day.values())
    print(f"=== P3 interleave scorer: {n_days} slate days, {len(log_rows)} logged slots "
          f"({n_both} shared/'both', {n_pairs} competitive pairs) ===\n")
    print(f"{'day':<12}{'pairs':>6}{'A2 wins':>9}{'A0 wins':>9}{'ties':>6}")
    for day, b in sorted(per_day.items()):
        print(f"{day:<12}{b['pairs']:>6}{b['a2']:>9}{b['a0']:>9}{b['tie']:>6}")

    llr, state = sprt_state(wins["a2"], wins["a0"])
    upper = math.log((1.0 - SPRT_BETA) / SPRT_ALPHA)
    decided = wins["a2"] + wins["a0"]
    print(f"\ntotals: A2 {wins['a2']} — A0 {wins['a0']} ({wins['tie']} ties discarded; "
          f"{decided} decided trials)")
    print(f"SPRT (H0 p=0.5 vs H1 p={SPRT_P1}, alpha=beta={SPRT_ALPHA}): "
          f"LLR={llr:+.3f}, boundaries +/-{upper:.3f}")
    if state == "flip":
        print("VERDICT: FLIP — the user's own verdicts prefer the quality-first arm. "
              "Set quality_review.rank_quality_first=true (separate commit), turn the "
              "interleave off, record the win in ADR-A9.")
    elif state == "keep":
        print("VERDICT: KEEP A0 — no actionable preference for A2. Turn the interleave "
              "off and record the negative result in ADR-A9 (with these counts).")
    else:
        need = max(0, math.ceil((upper - llr) / math.log(SPRT_P1 / 0.5)))
        print(f"VERDICT: CONTINUE — not enough signal yet (~{need} more A2-consecutive "
              f"wins to the flip boundary; keep labeling normally).")


if __name__ == "__main__":
    sys.exit(main())
