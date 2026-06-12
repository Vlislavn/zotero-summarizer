"""Retrospective check: does the goal blend improve the SLATE ordering?

The blend weights (0.4 goal / 0.15 prestige) were measured on the Library
blind-judge benchmark — a different surface (gate relevance over the unread
library) than the slate (heterogeneous composite_score over feed candidates).
This script validates the transfer on the slate's OWN ground truth: papers the
user explicitly kept (``user_approved``) vs trashed (``user_rejected``) in
``processed_feed_items``, ranked by composite-only vs the shared blend.

Usage (from the repo root, with the corpus model available):

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_slate_blend.py

Reads the real ``data/`` DBs via Settings (read-only); writes nothing.
Reports AUC (probability a kept paper outranks a trashed one) and P@10 for
both orderings. goal_sim is computed live from title+abstract with
``EmbeddingCache.affinity_and_goals`` — the same primitive the gate now uses.
"""
from __future__ import annotations

import json
import sqlite3
import sys


KEPT = ("user_approved", "selected", "black_swan")
TRASHED = ("user_rejected",)


def _rows(conn: sqlite3.Connection) -> list[dict]:
    placeholders = ",".join("?" * (len(KEPT) + len(TRASHED)))
    cur = conn.execute(
        f"""
        SELECT id, title, abstract, decision, composite_score, shap_contribs_json
        FROM processed_feed_items
        WHERE decision IN ({placeholders}) AND composite_score IS NOT NULL
        """,
        (*KEPT, *TRASHED),
    )
    return [dict(r) for r in cur.fetchall()]


def _citation_percentile(payload_json: str | None) -> float | None:
    raw = (payload_json or "").strip()
    if not raw:
        return None
    payload = json.loads(raw)
    aux = payload.get("aux_context") or {}
    pct = aux.get("citation_percentile")
    return float(pct) if pct is not None else None


def _auc(keys: list[float], labels: list[int]) -> float:
    pos = [k for k, y in zip(keys, labels) if y == 1]
    neg = [k for k, y in zip(keys, labels) if y == 0]
    if not pos or not neg:
        raise SystemExit("need both kept and trashed rows for AUC")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def _p_at(keys: list[float], labels: list[int], k: int) -> float:
    order = sorted(range(len(keys)), key=lambda i: -keys[i])[:k]
    return sum(labels[i] for i in order) / min(k, len(order))


def main() -> None:
    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.models import GoalsConfig
    from zotero_summarizer.services.model.rank_blend import blend_scores
    from zotero_summarizer.storage.corpus import EmbeddingCache
    import yaml

    settings_ = get_settings()
    config = GoalsConfig.model_validate(
        yaml.safe_load(settings_.config_path.read_text())
    )
    cache = EmbeddingCache(settings_.corpus_db_path, config.corpus.embedding_model)

    conn = sqlite3.connect(f"file:{settings_.triage_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = _rows(conn)
    finally:
        conn.close()
    rows = [r for r in rows if (r["abstract"] or "").strip()]
    if len(rows) < 20:
        raise SystemExit(f"only {len(rows)} labeled rows with abstracts — too few to measure")

    labels = [0 if r["decision"] in TRASHED else 1 for r in rows]
    composite = [float(r["composite_score"]) for r in rows]
    goal: list[float | None] = []
    for i, r in enumerate(rows):
        _aff, sims = cache.affinity_and_goals(r["title"] or "", r["abstract"] or "")
        goal.append(max(sims.values()) if sims else None)
        if (i + 1) % 50 == 0:
            print(f"  embedded {i + 1}/{len(rows)}", file=sys.stderr)
    prestige = [_citation_percentile(r["shap_contribs_json"]) for r in rows]

    blended = blend_scores(composite, goal, prestige)
    n_goal = sum(1 for g in goal if g is not None)
    n_prest = sum(1 for p in prestige if p is not None)
    print(f"rows={len(rows)} kept={sum(labels)} trashed={len(labels) - sum(labels)} "
          f"goal_sim_present={n_goal} prestige_present={n_prest}")
    print(f"composite-only : AUC={_auc(composite, labels):.3f}  P@10={_p_at(composite, labels, 10):.2f}")
    print(f"blend (0.4/0.15): AUC={_auc(blended, labels):.3f}  P@10={_p_at(blended, labels, 10):.2f}")
    goal_only = [g if g is not None else min(x for x in goal if x is not None) for g in goal]
    print(f"goal_sim alone : AUC={_auc(goal_only, labels):.3f}  P@10={_p_at(goal_only, labels, 10):.2f}")


if __name__ == "__main__":
    main()
