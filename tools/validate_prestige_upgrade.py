"""One-off validation of the SOTA prestige-signal upgrade (NOT a committed tool).

Faithfully rescores the unread library with the freshly-trained gate and reports
the prestige distribution + the data-driven floor + the must/should banding
before vs after the floor — using the exact production helpers. Run:

    uv run python tools/validate_prestige_upgrade.py
"""
from __future__ import annotations

from zotero_summarizer.domain import apply_prestige_floor, score_to_priority
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.library import reading_queue as rq
from zotero_summarizer.services.model import classifier_persistence
from zotero_summarizer.settings import Settings

SCAN_LIMIT = rq._SCAN_LIMIT  # same window production scores (most-recent 500)
BATCH = 50


def main() -> None:
    settings = Settings.load()
    config = read_config(settings.config_path)
    gate = classifier_persistence.load_trained(
        classifier_persistence.DEFAULT_MODEL_DIR / f"{config.classifier_gate.model_name}.joblib"
    )
    reader = ZoteroReader(settings.zotero_data_dir)

    handled = rq._HANDLED_EMOJIS
    page = reader.get_items(limit=SCAN_LIMIT)
    todo = [
        it for it in page.get("items", [])
        if not any(t in handled for t in (it.get("tags") or []))
        and (it.get("abstract") or "").strip()
    ]
    print(f"unread library items with abstracts: {len(todo)} (scan window {SCAN_LIMIT})")

    records: list[dict] = []
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        preds = gate.predict(
            chunk, corpus_db_path=settings.corpus_db_path,
            goals_config=config, return_shap=True,
        )
        by_key = {p.item_key: p for p in preds}
        for it in chunk:
            pred = by_key.get(it["item_key"])
            if pred is None:
                continue
            sc = rq.scoring_from_prediction(pred)
            prestige, known = rq._entry_prestige({"scoring": sc})
            records.append({
                "title": (it.get("title") or "")[:70],
                "relevance_score": float(pred.raw_score),
                "prestige_score": prestige,
                "prestige_known": known,
            })
        print(f"  scored {min(start + BATCH, len(todo))}/{len(todo)}", end="\r")
    print()

    n = len(records)
    known = [r for r in records if r["prestige_known"]]
    cold = [r for r in records if not r["prestige_known"]]
    print(f"\nscored: {n}")
    print(f"  known prestige (has percentile): {len(known)} ({len(known)/n:.0%})")
    print(f"  cold-start / uncited (kept, never floored): {len(cold)} ({len(cold)/n:.0%})")

    pairs = [(r["prestige_score"], r["prestige_known"]) for r in records]
    floor = rq.prestige_floor(pairs)
    print(f"\nprestige floor (median of known) = {floor}")
    if known:
        ks = sorted(r["prestige_score"] for r in known)
        print(f"  known prestige range: {ks[0]:.2f} .. {ks[-1]:.2f}; "
              f"p25={ks[len(ks)//4]:.2f} p50={ks[len(ks)//2]:.2f} p75={ks[3*len(ks)//4]:.2f}")

    # Bands before vs after the floor.
    before = {"must_read": 0, "should_read": 0, "could_read": 0, "dont_read": 0}
    after = {"must_read": 0, "should_read": 0, "could_read": 0, "dont_read": 0}
    demoted = []
    for r in records:
        raw_band = score_to_priority(r["relevance_score"])
        eff_band = apply_prestige_floor(
            raw_band, r["prestige_score"],
            prestige_known=r["prestige_known"], floor=floor,
        )
        before[raw_band] += 1
        after[eff_band] += 1
        if eff_band != raw_band:
            demoted.append(r)
    print("\nband counts  raw -> floored:")
    for b in ("must_read", "should_read", "could_read", "dont_read"):
        print(f"  {b:12s} {before[b]:4d} -> {after[b]:4d}")

    print(f"\ndemoted by floor (known low-prestige top items): {len(demoted)}")
    for r in sorted(demoted, key=lambda r: -r["relevance_score"])[:10]:
        print(f"  rel={r['relevance_score']:.2f} prestige={r['prestige_score']:.2f}  {r['title']}")

    # Top-of-queue sanity: highest-relevance items + their prestige.
    print("\ntop 15 by relevance (rel | prestige | known | title):")
    for r in sorted(records, key=lambda r: -r["relevance_score"])[:15]:
        p = f"{r['prestige_score']:.2f}" if r["prestige_score"] is not None else "  — "
        print(f"  {r['relevance_score']:.2f} | {p} | {'Y' if r['prestige_known'] else 'n'} | {r['title']}")


if __name__ == "__main__":
    main()
