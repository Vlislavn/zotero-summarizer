# services/library — Stage-2 reading + feed review

Once papers are in your library, this domain ranks what to read next and powers
the deeper review/annotation surfaces, plus the Phase 1.14 feed-review queue.

```
unread library items ─reading_queue→ ranked "Read next" (gate score + reason)
        click ─> deep_review (full-text relevance) / quality_review (peer-review style)
        border_cache: cached active-learning "border" picks for Annotate
   feeds awaiting review ─review→ approve / reject / relabel ─> golden + pending
   review_detail: uniform "why this score?" payload for /api/golden/review-detail
```

| file | responsibility |
|---|---|
| `_ranking.py` | post-scoring queue ORDERING helpers (split from `reading_queue`): content de-dup (collapse repeated Zotero copies of one paper by normalized title, keep the best-ranked) + **goal-aware re-rank** (`_blended_sort` mixes the gate score with `goal_sim` so on-goal papers the gate under-ranks float up; `_GOAL_RERANK_WEIGHT`=0.4, measured to lift NDCG@10 0.38→0.72). Order-only — banding/tags stay from the gate score |
| `reading_queue.py` | rank unread library rows by a gate-relevance × goal-similarity blend (see `_ranking`); hide read items; de-dup duplicate copies; expose the score `distribution` (Library histogram, `by_band` quality-floored) + `read_score_cache` (relevance+prestige) + `prestige_floor` (re-exported from `_score_distribution`). The **displayed** queue stays the 500-window read-next view, but the score **cache is global**: a Rescore (`_compute_scores_into_cache`) scans the WHOLE library (read + unread, via `get_all_items`; no-abstract items skipped) so every scorable paper has a cached score |
| `_score_distribution.py` | pure histogram + prestige-floor helpers (`prestige_floor` = median of KNOWN prestige, where "known" = OpenAlex returned a field-normalized `citation_percentile`; cold-start/uncited → unknown → never floored). Split out to keep `reading_queue` ≤500 LOC; re-exported there |
| `score_tags.py` | `sync_rel_tags` — write `zs:rel/<band>` relevance tags onto scored library items so you can FILTER by relevance in Zotero (one backup; quality-floored; never touches priority/manual tags). `sync_score_ranks` — stamp a **whole-library** goal-blended rank into Zotero Call Number (`zr0001…`, via the `set_field` write) for EVERY paper (scorable first, no-abstract last; no dedup) so you can SORT your entire library by relevance in Zotero (tags only filter); reads the global score cache (Rescore first); backup-first. Both cover the whole library via `get_all_items` |
| `deep_review.py` | on-demand full-text deep review of top picks |
| `quality_review.py` | full-text, peer-review-style quality assessment |
| `border_cache.py` | disk cache + job state for active-learning border picks |
| `review.py` | Phase 1.14 feed-review service: approve/reject/relabel/apply |
| `review_summary.py` | summary reconstruction + golden-CSV append helpers (re-exported by `review`) |
| `review_detail.py` | compose the unified review-detail payload |

**Boundaries:** imports `zotero/` (pending), `golden/` (append labels), and
`model/`/`daily_select` for scores; standard services rules.
