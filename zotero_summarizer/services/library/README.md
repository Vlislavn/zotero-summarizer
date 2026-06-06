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
| `_ranking.py` | post-scoring queue ORDERING helpers (split from `reading_queue`): content de-dup (collapse repeated Zotero copies of one paper by normalized title, keep the best-ranked) + **relevance × goal × prestige re-rank** (`_blended_sort` mixes the gate score with `goal_sim` AND author/venue **prestige**, so on-goal AND high-quality papers the gate under-ranks float up — "best of the best on top"; `_GOAL_RERANK_WEIGHT`=0.4 measured to lift NDCG@10 0.38→0.72, `_PRESTIGE_RERANK_WEIGHT`=0.15 a secondary quality lift kept below the goal weight. Each optional term's weight **folds back into relevance when its signal is absent** — goals+prestige→0.45/0.40/0.15, goals only→0.60/0.40 (the measured baseline), prestige only→0.85/–/0.15, neither→pure relevance. Prestige **never penalises missing evidence**: a row with no KNOWN prestige (`_known_prestige` keys off `prestige_known`; cold-start/uncited/no-OpenAlex) is scored at the MEDIAN of the known set = "typical quality", mirroring the median-of-known `prestige_floor`) + `sort_unread` (the queue's NORMAL order: the blend whenever the gate is ready — so the prestige lift applies even with no goals set — else recency/priority via `_PRIORITY_RANK`). Order-only — banding/tags stay from the gate score |
| `_search.py` | Library **hybrid search** ("Meaning" mode): BM25 (`corpus_bm25`) + dense cosine (`corpus_read.query_affinity_for_items`) fused via Reciprocal Rank Fusion, then a local cross-encoder reranks the top-100 → top-50. Degradation ladder: rerank → (model loading/off) fusion → (no BM25) dense-only → (corpus off) empty (caller falls back to substring). `order_unread_semantic` is the thin seam `reading_queue` calls. Order + `search_score` only — gate relevance/banding untouched |
| `reading_queue.py` | rank library rows by a gate-relevance × goal-similarity blend (see `_ranking`); `semantic=true` + a `search` query instead ranks by **hybrid search** (`_search`) — only the ORDER changes (relevance/bands/histogram stay), substring is bypassed (collection/tag kept), result capped to the reranked top-50, response adds `semantic`/`reranked`/`reranker_loading`/`semantic_unavailable`; hide read items; de-dup duplicate copies (across the read+unread split); expose the score `distribution` (Library histogram, `by_band` quality-floored) + `read_score_cache` (relevance+prestige) + `prestige_floor` (re-exported from `_score_distribution`). The displayed queue now ranks the **WHOLE library** (via `get_all_items`, `include_abstract=False`; `limit` caps the returned list, the frontend reveals it incrementally), and the score **cache is global**: a Rescore (`_compute_scores_into_cache`) scans the whole library (read + unread; no-abstract items skipped). Scoring runs only on refresh; the cheap `is_running`/`last_error` seam backs the `reading-queue/status` poll so a Rescore doesn't re-read the library every tick |
| `_score_distribution.py` | pure histogram + prestige-floor helpers (`prestige_floor` = median of KNOWN prestige, where "known" = OpenAlex returned a field-normalized `citation_percentile`; cold-start/uncited → unknown → never floored). Split out to keep `reading_queue` ≤500 LOC; re-exported there. NB a cold-start paper may now show a *provisional* author-based prestige badge (`scoring_from_prediction` surfaces `cold_start_prestige`) yet still counts as UNKNOWN to the floor (`known` keys off `citation_percentile`), so it is never demoted nor pollutes the median |
| `score_tags.py` | `sync_rel_tags` — write `zs:rel/<band>` relevance tags onto scored library items so you can FILTER by relevance in Zotero (one backup; quality-floored; never touches priority/manual tags). `sync_score_ranks` — stamp a **whole-library** goal-blended rank into Zotero Call Number (`zr0001…`, via the `set_field` write) for EVERY paper (scorable first, no-abstract last; no dedup) so you can SORT your entire library by relevance in Zotero (tags only filter); reads the global score cache (Rescore first); backup-first. Both cover the whole library via `get_all_items` |
| `fulltext.py` | fetch arXiv full-text PDFs and attach them natively to Zotero (`add_attachment` write). `fetch_fulltext_for_items` (initial-add hook, via `daily_actions.add_to_library`, best-effort) + `start_bulk`/`status` (the Library "Fetch full text → Zotero" button: background job over the whole library, skips papers that already have a PDF or no arXiv link). Reuses `pdf_fetch.resolve_pdf_url`(arXiv)/`fetch_pdf`; backup-first + connector-guarded |
| `deep_review.py` | on-demand full-text deep review of top picks |
| `quality_review.py` | full-text, peer-review-style quality assessment |
| `border_cache.py` | disk cache + job state for active-learning border picks |
| `review.py` | Phase 1.14 feed-review service: approve/reject/relabel/apply |
| `review_summary.py` | summary reconstruction + golden-CSV append helpers (re-exported by `review`) |
| `review_detail.py` | compose the unified review-detail payload |

**Boundaries:** imports `zotero/` (pending), `golden/` (append labels), and
`model/`/`daily_select` for scores; standard services rules.
