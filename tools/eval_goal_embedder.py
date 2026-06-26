"""Embedder shoot-out for the goal_sim signal: is MiniLM-L6 leaving lift on the table?

goal_sim (max cosine of a paper's title+abstract to the research-goal texts) is
the system's strongest ranking lever (blind-judge Spearman 0.72 vs the gate's
0.40), yet it is computed by ``corpus.embedding_model`` — currently
all-MiniLM-L6-v2, a 2021 general-purpose 384-d model — while the gate itself
uses SPECTER2 and hybrid search uses a bge cross-encoder. This script measures,
on the slate's own ground truth (papers the user kept vs trashed in
``processed_feed_items``), whether a stronger LOCALLY-CACHED embedder lifts the
goal signal:

  * ``minilm``   — sentence-transformers/all-MiniLM-L6-v2 (current production)
  * ``bge-m3``   — BAAI/bge-m3 (cached; loads by name via EmbeddingCache, so a
                   win here is closable by flipping ``corpus.embedding_model``)
  * ``specter2`` — the gate's own encoder + proximity adapter (paper-paper
                   model; goals are short queries, so this may NOT transfer)

Each embedder is used exactly the way production uses the corpus model: items
as ``EmbeddingCache._build_text`` ("title. abstract"), goals as the raw goal
text, vectors L2-normalized, goal_sim = max cosine over goals (row_goal_sim).

Usage (repo root; reads data/ read-only, writes nothing):

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_goal_embedder.py
"""
from __future__ import annotations

import json
import sqlite3
import sys

import numpy as np

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


def _build_text(title: str, abstract: str) -> str:
    # Mirror EmbeddingCache._build_text exactly — the experiment must score the
    # text production would embed, not a variant.
    return f"{(title or '').strip()}. {(abstract or '').strip()}".strip()


def _st_goal_sims(model_name: str, goals: list[str], texts: list[str]) -> list[float]:
    """goal_sim per text via a sentence-transformers model, production-style."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    # bge-m3 ships with an 8192-token window; attention at that length OOMs on
    # MPS (64 GiB buffer ask). 512 covers title+abstract (MiniLM truncates at
    # 256) and is what production would have to cap to as well.
    model.max_seq_length = min(int(model.max_seq_length or 512), 512)
    gvecs = model.encode(goals, normalize_embeddings=True, show_progress_bar=False)
    tvecs = model.encode(
        texts, normalize_embeddings=True, batch_size=8, show_progress_bar=True
    )
    sims = np.asarray(tvecs) @ np.asarray(gvecs).T  # (N, G)
    return [float(s) for s in sims.max(axis=1)]


def _specter2_goal_sims(
    goals: list[str], pairs: list[tuple[str, str]]
) -> list[float]:
    """goal_sim via the gate's SPECTER2+proximity path (paper-side batch embed;
    goals embedded as title-only docs — SPECTER2 has no query mode without the
    adhoc-query adapter, which is exactly what this measures)."""
    from zotero_summarizer.services.model.classifier_embed import compute_embeddings_batch

    gmat = compute_embeddings_batch([(g, "") for g in goals])
    tmat = compute_embeddings_batch(pairs)

    def _norm(m: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(m, axis=1, keepdims=True)
        return m / np.where(n > 0, n, 1.0)

    sims = _norm(tmat) @ _norm(gmat).T
    return [float(s) for s in sims.max(axis=1)]


def main() -> None:
    import yaml

    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.services.model.rank_blend import blend_scores

    settings_ = get_settings()
    config = yaml.safe_load(settings_.config_path.read_text())
    goals = [str(g) for g in (config.get("research_goals") or []) if str(g).strip()]
    if not goals:
        raise SystemExit("no research_goals configured — nothing to measure")

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
    prestige = [_citation_percentile(r["shap_contribs_json"]) for r in rows]
    texts = [_build_text(r["title"] or "", r["abstract"] or "") for r in rows]
    pairs = [(r["title"] or "", r["abstract"] or "") for r in rows]

    print(f"rows={len(rows)} kept={sum(labels)} trashed={len(labels) - sum(labels)} goals={len(goals)}")
    print(f"baseline composite-only : AUC={_auc(composite, labels):.3f}  "
          f"P@10={_p_at(composite, labels, 10):.2f}  P@20={_p_at(composite, labels, 20):.2f}")

    embedders: list[tuple[str, callable]] = [
        ("minilm (current)", lambda: _st_goal_sims(
            "sentence-transformers/all-MiniLM-L6-v2", goals, texts)),
        ("bge-m3", lambda: _st_goal_sims("BAAI/bge-m3", goals, texts)),
        ("specter2+proximity", lambda: _specter2_goal_sims(goals, pairs)),
    ]
    for name, run in embedders:
        print(f"\n=== {name} ===", file=sys.stderr)
        sims = run()
        blended = blend_scores(composite, [float(s) for s in sims], prestige)
        print(f"{name:<22}: goal_sim AUC={_auc(sims, labels):.3f}  "
              f"P@10={_p_at(sims, labels, 10):.2f}  P@20={_p_at(sims, labels, 20):.2f}  "
              f"| blend AUC={_auc(blended, labels):.3f}  P@10={_p_at(blended, labels, 10):.2f}")


if __name__ == "__main__":
    main()
