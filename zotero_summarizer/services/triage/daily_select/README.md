# services/triage/daily_select — the Today slate

Assembles the daily "Today" slate: a small, role-mixed set of feed papers
(`GET /api/daily`). Reads scored rows, normalizes them to candidates, then
greedily allocates K slots across roles so the slate isn't all one flavor.

```
processed_feed_items ─_querying.open_ro→ rows ─_candidate→ normalized candidates
                                                  │
                       _allocation (greedy): model picks + surprise + diversity
                                                  ▼
                                   _dataclasses.DailySlate  ──> /api/daily
```

| file | responsibility |
|---|---|
| `__init__.py` | public surface: `assemble_daily_slate`, `count_awaiting_unhandled` |
| `_querying.py` | read-only SQLite access (delegates to `_common.connect_sqlite_ro`) |
| `_candidate.py` | raw DB row → normalized candidate dict (scores, provenance); `row_*` string accessors share `_row_str`/`_obj_field`; `row_prestige` prefers OpenAlex field-normalized `citation_percentile` (already [0,1]) → LLM prestige → h-index → composite |
| `_allocation.py` | greedy role allocator (model / surprise / diversity slots) |
| `_dataclasses.py` | frozen result types (`DailySlate`, `SlatePaper` — includes `abstract` and `pub_year` for the Today card) |
