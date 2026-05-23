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
| `reading_queue.py` | rank unread library rows by gate relevance; hide read items |
| `deep_review.py` | on-demand full-text deep review of top picks |
| `quality_review.py` | full-text, peer-review-style quality assessment |
| `border_cache.py` | disk cache + job state for active-learning border picks |
| `review.py` | Phase 1.14 feed-review service: approve/reject/relabel/apply |
| `review_summary.py` | summary reconstruction + golden-CSV append helpers (re-exported by `review`) |
| `review_detail.py` | compose the unified review-detail payload |

**Boundaries:** imports `zotero/` (pending), `golden/` (append labels), and
`model/`/`daily_select` for scores; standard services rules.
