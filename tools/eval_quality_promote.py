"""Eval: does quality→must_read promotion (``rank_blend.promote_band``) surface the
papers the user actually wants, WITHOUT flooding must_read with junk?

The gate's regressor compresses scores → the must_read band (≥4.5) is never reached
on its own (0/69 recall), so must_read is empty even for great papers. ``promote_band``
lifts a paper to must_read only when quality AND goal-match AND the gate AGREE. This
tool measures that rule against FIREWALLED ground truth before the flag is flipped
(the gate the config docstring promised).

Ground truth = the user's OWN reading-priority verdicts (``label_verdicts``,
``source='user'``), joined by Zotero ``item_key`` to:
  - the deep-review GRADE (``deep_reviews.json`` → ``quality.grade``), and
  - the gate ``goal_sim`` + ``relevance_score`` (``reading_queue.json`` cache).

For each (goal_floor, relevance_floor) operating point it applies the REAL
``promote_band`` and reports over the promoted set:
  - precision(kept)  = P(user verdict ∈ must/should/could | promoted)  — higher is safer
  - flooding         = # promoted→must the user marked dont_read        — the failure mode
  - coverage         = how many papers the rule promotes at all

Ship the promotion ON only at a floor with ZERO flooding and high precision.

Run:  uv run python tools/eval_quality_promote.py [--goal-floor 0.55]
      uv run python tools/eval_quality_promote.py --selfcheck
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from zotero_summarizer.services.model.rank_blend import promote_band
from zotero_summarizer.models.triage import ReadingPriority

MUST = ReadingPriority.MUST_READ.value
SHOULD = ReadingPriority.SHOULD_READ.value
KEPT = {MUST, SHOULD, ReadingPriority.COULD_READ.value}
WANT = {MUST, SHOULD}
DONT = ReadingPriority.DONT_READ.value


def _model_dir() -> Path:
    from zotero_summarizer.services.model.classifier_persistence import DEFAULT_MODEL_DIR
    return DEFAULT_MODEL_DIR


def load_rows(triage_db: Path, model_dir: Path) -> list[dict]:
    """Join user verdicts × grade × (goal_sim, relevance) by item_key. Fail loud if a
    required artifact is missing — a silent empty join would read as 'rule is safe'."""
    reviews_path = model_dir / "deep_reviews.json"
    queue_path = model_dir / "reading_queue.json"
    for p in (triage_db, reviews_path, queue_path):
        if not p.exists():
            raise FileNotFoundError(f"required artifact missing: {p}")

    con = sqlite3.connect(f"file:{triage_db}?mode=ro", uri=True)
    try:
        verdicts = {
            k: str(p or "").strip()
            for k, p, s in con.execute(
                "SELECT item_key, user_priority, source FROM label_verdicts"
            )
            if k and str(s or "").strip() == "user"
        }
    finally:
        con.close()

    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    reviews = reviews.get("reviews") or reviews
    grades = {
        k: str((v.get("quality") or {}).get("grade") or "").strip().upper()
        for k, v in reviews.items()
        if (v.get("quality") or {}).get("grade")
    }

    scores = (json.loads(queue_path.read_text(encoding="utf-8")).get("scores")) or {}

    rows: list[dict] = []
    for k in set(verdicts) & set(grades) & set(scores):
        entry = scores[k] or {}
        gs = entry.get("goal_sim")
        rel = entry.get("relevance_score")
        if rel is None:
            rel = (entry.get("scoring") or {}).get("composite_score")
        if gs is None or rel is None:
            continue
        rows.append({
            "grade": grades[k], "goal_sim": float(gs),
            "relevance": float(rel), "verdict": verdicts[k],
        })
    return rows


def evaluate(rows: list[dict], *, goal_floor: float, rel_floor: float) -> dict:
    """Apply the REAL promote_band to each row; tally precision / flooding / coverage
    over the promoted set. Pure (no IO) so it is unit-checkable — see _selfcheck."""
    to_must, to_should = [], []
    for r in rows:
        decision = promote_band(
            ReadingPriority.COULD_READ.value,  # neutral raw band → isolate the promotion
            grade=r["grade"], relevance_score=r["relevance"], goal_sim=r["goal_sim"],
            goal_sim_floor=goal_floor, relevance_floor=rel_floor,
        )
        if decision == MUST:
            to_must.append(r)
        elif decision == SHOULD:
            to_should.append(r)

    def frac(subset, pos):
        if not subset:
            return None
        hit = sum(1 for r in subset if r["verdict"] in pos)
        return hit / len(subset)

    return {
        "goal_floor": goal_floor, "rel_floor": rel_floor,
        "n_must": len(to_must), "must_precision_kept": frac(to_must, KEPT),
        "flooding": sum(1 for r in to_must if r["verdict"] == DONT),
        "n_should": len(to_should), "should_precision_kept": frac(to_should, KEPT),
    }


def _fmt(x):
    return "—" if x is None else f"{x:.2f}"


def main() -> None:
    from zotero_summarizer.services._common import settings as get_settings

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goal-floor", type=float, default=0.55)
    ap.add_argument("--rel-floors", type=float, nargs="+", default=[3.5, 3.25, 3.0, 2.75, 2.5])
    ap.add_argument("--selfcheck", action="store_true", help="run the metric self-check and exit")
    args = ap.parse_args()
    if args.selfcheck:
        _selfcheck()
        print("selfcheck OK")
        return

    settings = get_settings()
    rows = load_rows(settings.triage_db_path, _model_dir())
    print(f"joined rows (verdict ∩ grade ∩ gate) = {len(rows)}\n")
    if not rows:
        print("no joinable ground truth yet — verdict more reviewed papers first.")
        return
    header = f"{'rel_floor':>9} {'goal_floor':>10} | {'→must':>5} {'must-prec':>9} {'flood':>5} | {'→should':>7} {'should-prec':>11}"
    print(header)
    print("-" * len(header))
    for rf in args.rel_floors:
        m = evaluate(rows, goal_floor=args.goal_floor, rel_floor=rf)
        print(f"{rf:>9} {args.goal_floor:>10} | {m['n_must']:>5} {_fmt(m['must_precision_kept']):>9} "
              f"{m['flooding']:>5} | {m['n_should']:>7} {_fmt(m['should_precision_kept']):>11}")
    print("\nPick the LOWEST rel_floor with flooding=0 and high precision "
          "(more coverage without promoting junk).")


def _selfcheck() -> None:
    # 2 promote-to-must rows (one kept, one dont_read=flooding) + 1 below the floor.
    rows = [
        {"grade": "A", "goal_sim": 0.9, "relevance": 4.0, "verdict": MUST},   # promoted, kept
        {"grade": "A", "goal_sim": 0.9, "relevance": 4.0, "verdict": DONT},   # promoted, FLOOD
        {"grade": "A", "goal_sim": 0.9, "relevance": 2.0, "verdict": MUST},   # below floor → not promoted
    ]
    m = evaluate(rows, goal_floor=0.55, rel_floor=3.5)
    assert m["n_must"] == 2, m
    assert m["flooding"] == 1, m
    assert abs(m["must_precision_kept"] - 0.5) < 1e-9, m


if __name__ == "__main__":
    main()
