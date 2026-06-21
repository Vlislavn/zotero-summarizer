# integrations — external-system adapters

Thin, low-level clients for everything outside the app: the local Zotero
SQLite DB, PDFs, the LLM, OpenAlex (prestige), Unpaywall (OA PDFs). No business
logic — just I/O with clear types.

```
services/ ─calls→ integrations/ ─talks to→  Zotero DB | PDFs | LLM API | OpenAlex | Unpaywall
```

| file | responsibility |
|---|---|
| `zotero_read.py` | `ZoteroReader`: connection/execute infra + collection helpers. `_sort_collection_nodes` pins the two workflow collections to the top of every collections list (`_PINNED_COLLECTIONS`: rank 0 = Inbox landing zone, rank 1 = the read-next queue — pattern mirrors the frontend `CollectionEditor` `READ_NEXT_RE`), then alphabetical; applied recursively |
| `_zotero_read_items.py` · `_zotero_read_lookup.py` · `_zotero_read_feeds.py` | reader query mixins (items/detail — now also selects Zotero `typeName` as `item_type`, a weak paper-type prior for deep-review type detection · `get_all_items` which paginates past the per-call 500 clamp for whole-library passes · find/membership/tags — DOI dedup matches all `domain.normalize_doi` variants · feeds) |
| `_zotero_read_common.py` | `ZoteroReadError` + arXiv/sanitize helpers + `_NON_BIBLIOGRAPHIC_TYPES_SQL` (the single `('attachment','note','annotation')` exclusion every "library items" query shares so PDF annotations never appear as papers) + `_USER_LIBRARY_ID_SELECT` (the single `type='user'` library scope every whole-library read injects — Zotero keeps ~dozens of `type='feed'` RSS libraries in the same `items` table, so an unscoped read leaks feed items into the corpus/ranker/tag writes/full-text; this caused cross-library 403 attachments) (leaf) |
| `zotero_write.py` | `ZoteroWriter`: WAL-consistent backup (+ prune) + the apply-changes dispatcher |
| `_zotero_write_items.py` · `_zotero_write_fields.py` · `_zotero_write_attachments.py` · `_zotero_write_tags.py` · `_zotero_write_collections.py` | writer mixins (item creation/materialization · `set_field` single-field upsert e.g. Call Number · `add_attachment` — native imported_url PDF: copies the file to `storage/<key>/` then inserts the attachment item + itemAttachments with `synced=0`/`syncState=0`/`storageHash` NULL so a syncing Zotero hashes + uploads it · tag/note + helpers · collections) |
| `_zotero_write_common.py` | `ZoteroWriteError` + LOGGER + `resolve_user_library_item_id` — the single guard every write that targets an item by key routes through, scoping resolution to `type='user'` so a feed item's key can never be mutated/parented into the user library (`required=False` for the best-effort batch remove). Mirrors `_USER_LIBRARY_ID_SELECT` on the read side (leaf) |
| `pdf.py` · `pdf_fetch.py` | extract text from a local PDF; fetch OA PDFs (size/timeout caps) |
| `browser_fetch.py` | browser-driven PDF fetch for **university institutional access** (the review fleet's non-arXiv / paywalled rung). Lazy-imports **patchright** (drop-in patched Playwright that passes Cloudflare; falls back to `playwright`), drives a **persistent profile** the user logs into once (`open_login_window`, headed) so EZproxy/Shibboleth/`cf_clearance` cookies persist; `fetch_pdf_via_browser` then reuses the session headless — direct authed `context.request.get` → response interception — and validates `%PDF`/size into `pdf_fetch`'s shared cache. Single browser at a time (module lock — RAM + Chromium `SingletonLock`). With `reuse_safari_cookies` it injects the user's existing **Safari** session (`browser-cookie3`, `_load_safari_cookies` → `context.add_cookies`) so a paper they can open in Safari fetches without a second login — degrades to `[]` when the dep is missing or the store is unreadable (no Full Disk Access). Optional `[browser]` extra; degrades to `None` when absent (authorized best-effort, the fleet reports it honestly). `is_available` / `is_logged_in` feed the Settings readiness panel (leaf) |
| `llm.py` | `LLMClient` protocol + `InstrumentedLLMClient` (logging wrapper for OpenAI-compatible clients) |
| `llm_anthropic.py` | `AnthropicLLMClient`: native Anthropic messages-API client implementing the same `LLMClient` protocol (`.prompt` / `.pydantic_prompt`). Lazy `import anthropic`. |
| `llm_models.py` | List a provider's available model ids for the Settings picker: `list_openai_models` (httpx `GET {base_url}/models`) · `list_anthropic_models` (SDK `models.list`). Read-only; key passed in already resolved. |
| `openalex.py` · `openalex_cache.py` | prestige lookups + their SQLite cache. `OpenAlexClient(allow_network=False)` is cache-only (a miss → None, no HTTP) for interactive request paths that must not block on a search. For **cold-start** works (no own percentile) `_enrich_with_authors` also fetches each author's field-normalized standing (`max_author_field_percentile` = median of the author's works' `citation_normalized_percentile`) — skipped for established papers to bound the extra `/works` calls |
| `unpaywall.py` | DOI → open-access PDF URL |

**Boundaries:** must NOT import `services/` or `api/` (enforced by pre-commit).
Adapters are leaves — they depend only on stdlib, third-party clients, and
`models`/`domain`.
