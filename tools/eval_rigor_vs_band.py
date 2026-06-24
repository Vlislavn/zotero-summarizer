"""P4 — validate the INCUMBENT abstract-rigor signal before it earns ranking weight.

The plan's Phase 4 (per the user's "validate the incumbent" choice): do NOT add a
new {strong|ok|weak} field. Instead measure whether the EXISTING abstract-time
``TriageDimensions.methodological_rigor`` (1-5, ``models/triage.py:46``, already
feeding composite) agrees with the full-text deep-review BAND — and ship it as a
ranking signal ONLY if it passes BOTH gates the user chose ("both, band primary"):

  * PRIMARY (validity): weighted Cohen's κ + Spearman of rigor vs the band
    (flag<neutral<highlight; the non-ordinal ``uncertain`` is held OUT as its own
    cell), with special attention to the clinically harmful false-`strong`
    (rigor≥4) on a deep-`flag` cell. The band is TRIPOD/PROBAST-grounded and
    author/citation-blind, so it — not engagement — catches a weak clinical paper.
  * SECONDARY (engagement): does rigor discriminate user_approved vs user_rejected
    (the firewalled labels, same as eval_slate_blend).

Range-restriction caveat (a 10-expert review point): the rigor↔band overlap is the
reviewed TOP-K, where weak papers are largely absent. If too few flag/weak
exemplars exist the signal is **UNVALIDATABLE for ranking → stays display-only**.

The overlap is EMPTY at ship (rigor is abstract-time + forward-only, the band is
top-K), so the pairs are produced by a BACKFILL: re-running the abstract triage
over the deep-reviewed papers. That LLM pass is HEAVY — run user-coordinated,
foreground (see memory-safe-runs). Pure stats below are unit-tested offline.

Usage (from the repo root):

    KMP_DUPLICATE_LIB_OK=TRUE uv run python tools/eval_rigor_vs_band.py
"""
from __future__ import annotations

import sys

# rigor 1-5 binned to the band's 3 ordinal levels for a like-for-like κ.
RIGOR_WEAK_MAX = 2     # 1-2 → weak  (band: flag)
RIGOR_STRONG_MIN = 4   # 4-5 → strong (band: highlight)
BAND_ORDINAL = {"flag": 0, "neutral": 1, "highlight": 2}  # uncertain held OUT
MIN_FLAG_EXEMPLARS = 10  # below this the validity gate is range-restricted → display-only


# --- pure agreement stats (unit-tested in tests/test_eval_rigor.py) -----------

def _bin_rigor(rigor: float) -> int:
    if rigor <= RIGOR_WEAK_MAX:
        return 0
    if rigor >= RIGOR_STRONG_MIN:
        return 2
    return 1


def _band_ordinal(band: str | None) -> int | None:
    """flag/neutral/highlight → 0/1/2; uncertain/unknown → None (held out)."""
    return BAND_ORDINAL.get((band or "").lower())


def _weighted_kappa(pairs: list[tuple[int, int]], *, k: int = 3, power: int = 2) -> float:
    """Cohen's weighted κ over k ordinal categories (quadratic weights by default).
    ``pairs`` = (rater_a_cat, rater_b_cat). Raises on an empty / single-rater set."""
    n = len(pairs)
    if n == 0:
        raise ValueError("weighted kappa needs ≥1 pair")
    obs = [[0.0] * k for _ in range(k)]
    row_marg = [0.0] * k
    col_marg = [0.0] * k
    for a, b in pairs:
        obs[a][b] += 1.0
        row_marg[a] += 1.0
        col_marg[b] += 1.0
    denom = (k - 1) ** power
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = (abs(i - j) ** power) / denom
            exp = row_marg[i] * col_marg[j] / n
            num += w * obs[i][j]
            den += w * exp
    if den == 0:
        return 1.0  # no expected disagreement (a degenerate single-category set)
    return 1.0 - num / den


def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # average rank for ties (1-based)
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("spearman needs ≥2 paired points")
    rx, ry = _rank(xs), _rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n))
    vy = sum((ry[i] - my) ** 2 for i in range(n))
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy) ** 0.5


def _false_strong_on_flag(pairs: list[tuple[float, str]]) -> int:
    """The clinically harmful cell: abstract says STRONG (rigor≥4) but the
    full-text band is FLAG. ``pairs`` = (rigor, band)."""
    return sum(1 for r, b in pairs if r >= RIGOR_STRONG_MIN and (b or "").lower() == "flag")


def _auc(keys: list[float], labels: list[int]) -> float:
    pos = [k for k, y in zip(keys, labels) if y == 1]
    neg = [k for k, y in zip(keys, labels) if y == 0]
    if not pos or not neg:
        raise ValueError("AUC needs both classes")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def main() -> None:
    import sqlite3

    from zotero_summarizer.services._common import settings as get_settings
    from zotero_summarizer.models import GoalsConfig, SummarizeRequest
    from zotero_summarizer.services.library import deep_review
    from zotero_summarizer.services.triage import summarization
    import yaml

    settings_ = get_settings()
    GoalsConfig.model_validate(yaml.safe_load(settings_.config_path.read_text()))

    reviews = deep_review._read_all()
    banded = {k: (e.get("quality") or {}).get("quality_band")
              for k, e in reviews.items() if (e.get("quality") or {}).get("quality_band")}
    print(f"deep-reviewed papers with a band: {len(banded)}")
    if not banded:
        raise SystemExit("no banded deep reviews — nothing to validate against")

    # Join band → the feed row (title/abstract + firewalled label) via the library key.
    conn = sqlite3.connect(f"file:{settings_.triage_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT title, abstract, decision, materialized_zotero_key "
            "FROM processed_feed_items WHERE materialized_zotero_key IS NOT NULL"
        )
        feed = {r["materialized_zotero_key"]: dict(r) for r in cur.fetchall()}
    finally:
        conn.close()

    paired_keys = [k for k in banded if k in feed and (feed[k]["abstract"] or "").strip()]
    print(f"joinable (band + abstract) papers: {len(paired_keys)}")
    flag_n = sum(1 for k in paired_keys if (banded[k] or "").lower() == "flag")
    if flag_n < MIN_FLAG_EXEMPLARS:
        print(f"*** RANGE-RESTRICTED *** only {flag_n} deep-`flag` exemplars in the reviewed "
              f"overlap (< {MIN_FLAG_EXEMPLARS}) — the validity gate cannot be measured; "
              f"keep rigor DISPLAY-ONLY (record this n).")

    # BACKFILL (HEAVY): re-run abstract triage to get rigor for each paired paper.
    print(f"backfilling abstract-rigor for {len(paired_keys)} papers (LLM pass)…", file=sys.stderr)
    rigor_band: list[tuple[float, str]] = []
    for i, k in enumerate(paired_keys):
        row = feed[k]
        req = SummarizeRequest(title=row["title"] or "", abstract=row["abstract"] or "")
        resp = summarization.run_abstract_pipeline(req)
        dims = resp.triage_dimensions
        rigor = float(getattr(dims, "methodological_rigor", 0) or 0) if dims else 0.0
        rigor_band.append((rigor, banded[k]))
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(paired_keys)}", file=sys.stderr)

    # PRIMARY (validity): κ + Spearman vs the ordinal band, uncertain held out.
    ordinal = [(_bin_rigor(r), _band_ordinal(b)) for r, b in rigor_band]
    kept = [(a, b) for a, b in ordinal if b is not None]
    if len(kept) >= 2:
        kappa = _weighted_kappa(kept)
        rho = _spearman([float(a) for a, _ in kept], [float(b) for _, b in kept])
        print(f"\nPRIMARY validity (n={len(kept)}, uncertain held out): "
              f"weighted-κ={kappa:.3f}  Spearman ρ={rho:.3f}")
    print(f"clinically-harmful cell: {_false_strong_on_flag(rigor_band)} papers rated "
          f"STRONG (rigor≥{RIGOR_STRONG_MIN}) by the abstract but FLAG by the full text")

    # SECONDARY (engagement): does rigor discriminate kept vs trashed?
    eng = [(r, feed[k]["decision"]) for (r, _), k in zip(rigor_band, paired_keys)]
    labels = [1 if d == "user_approved" else 0 for _, d in eng if d in ("user_approved", "user_rejected")]
    rig = [r for (r, d) in eng if d in ("user_approved", "user_rejected")]
    if labels and 0 < sum(labels) < len(labels):
        print(f"SECONDARY engagement (n={len(labels)}): rigor AUC(kept vs trashed)={_auc(rig, labels):.3f}")

    print("\nGATE: grant rigor a ranking weight ONLY if PRIMARY κ is materially >0 AND "
          "SECONDARY AUC>0.5 AND not range-restricted; else display-only (record the numbers).")


if __name__ == "__main__":
    main()
