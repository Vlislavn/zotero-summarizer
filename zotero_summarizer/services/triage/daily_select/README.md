# services/triage/daily_select — the Today slate

Assembles the daily "Today" slate: a small, role-mixed set of feed papers
(`GET /api/daily`). Reads scored rows, normalizes them to candidates, then
greedily allocates K slots across roles so the slate isn't all one flavor.

```
processed_feed_items ─_querying.open_ro→ rows ─drop handled ─drop trashed-GUID ─drop content dupes─┐
                                                  │ (trashed-GUID = never-show-again; content by DOI/arXiv vs decided/library)
                                       ─_candidate→ normalized candidates        │
                                                  │ (+ _relevance.build_why chips)│
                       _allocation (greedy): model picks + surprise + diversity ◀┘
                                                  ▼
                                   _dataclasses.DailySlate  ──> /api/daily
```

**Duplicate protection (slate guard).** Identity dedup (`dedup_keep_newest`, by
GUID) only catches the *same* RSS item. A paper re-arriving under a different
GUID — or one the user already trashed/added, or already in the library — would
otherwise reappear. `_fetch_primary_unhandled` (the single source consumed by
both the slate and the `awaiting` counter) drops, by **DOI/arXiv only**, any
awaiting card that matches a paper in a "blocking" decision
(`fetch_decided_content_keys`: selected / black_swan / user_approved /
user_rejected / dedup_library / dedup_processed, or a `materialized_zotero_key`)
and collapses same-paper awaiting copies (keep newest). DOI/arXiv-only ⇒ a card
with no identifier is never dropped (no false positives). The triage daemon does
the matching root-cause dedup at ingest (`feeds.dedup_against_processed`).

**Trash → never show again (GUID guard).** Content dedup only helps papers that
carry a DOI/arXiv, and a trashed paper re-arrives under a *fresh* `feed_item_id`
(so `_drop_handled` misses it). `_drop_trashed_guids` drops any row whose stable
GUID matches a paper the user explicitly threw away — `user_rejected` (trashed
from Today) or a `trashed`/`deleted_all` `final_outcome` (trashed inside Zotero)
— the one identifier that survives re-ingestion, so an id-less thrown-away paper
stays gone. Mirrors the daemon's trashed-GUID suppression in `feeds._tick_dedup`.

The `model` quota scales with `K` (`assemble_daily_slate`: `model = max(3, K-2)`,
surprise/diversity stay at 1), so a larger `K` actually returns more cards — the
old fixed 3/1/1 default capped every slate at 5 regardless of `K`.

| file | responsibility |
|---|---|
| `__init__.py` | public surface: `assemble_daily_slate` (K-scaled model quota), `count_awaiting_unhandled`; `_drop_handled` + `_drop_content_dupes` (DOI/arXiv guard, `_BLOCKING_DECISIONS`) + `_drop_trashed_guids` (stable-GUID "never show again" guard, `_TRASH_DECISIONS`/`_TRASH_OUTCOMES`) |
| `_querying.py` | read-only SQLite access (delegates to `_common.connect_sqlite_ro`); `fetch_decided_content_keys` returns normalized DOI/arXiv sets for decided / in-library papers (now also blocks on a trashing `final_outcome`, not just `decision`) + `fetch_trashed_guids` returns the stable GUIDs of explicitly-trashed papers for the never-show-again guard |
| `_candidate.py` | raw DB row → normalized candidate dict (scores, provenance, `why` chips); `row_*` string accessors share `_row_str`/`_obj_field`; `row_prestige` prefers OpenAlex field-normalized `citation_percentile` (already [0,1]) → LLM prestige → h-index → composite |
| `_relevance.py` | heuristic, no-LLM `build_why` — plain-language "why it matters" reason chips from goal match / model relevance / author prestige / citations / surprise (thresholds reuse `domain`/`surprise` constants) |
| `_allocation.py` | greedy role allocator (model / surprise / diversity slots) |
| `_dataclasses.py` | frozen result types (`DailySlate`, `SlatePaper` — includes `abstract`, `pub_year`, and `why` chips for the Today card) |
