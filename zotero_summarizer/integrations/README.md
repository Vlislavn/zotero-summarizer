# integrations — external-system adapters

Thin, low-level clients for everything outside the app: the local Zotero
SQLite DB, PDFs, the LLM, OpenAlex (prestige), Unpaywall (OA PDFs). No business
logic — just I/O with clear types.

```
services/ ─calls→ integrations/ ─talks to→  Zotero DB | PDFs | LLM API | OpenAlex | Unpaywall
```

| file | responsibility |
|---|---|
| `zotero_read.py` | `ZoteroReader`: connection/execute infra + collection helpers |
| `_zotero_read_items.py` · `_zotero_read_lookup.py` · `_zotero_read_feeds.py` | reader query mixins (items/detail + `get_all_items` which paginates past the per-call 500 clamp for whole-library passes · find/membership/tags — DOI dedup matches all `domain.normalize_doi` variants · feeds) |
| `_zotero_read_common.py` | `ZoteroReadError` + arXiv/sanitize helpers + `_NON_BIBLIOGRAPHIC_TYPES_SQL` (the single `('attachment','note','annotation')` exclusion every "library items" query shares so PDF annotations never appear as papers) (leaf) |
| `zotero_write.py` | `ZoteroWriter`: backup + the apply-changes dispatcher |
| `_zotero_write_items.py` · `_zotero_write_fields.py` · `_zotero_write_tags.py` · `_zotero_write_collections.py` | writer mixins (item creation/materialization · `set_field` single-field upsert e.g. Call Number · tag/note + helpers · collections) |
| `_zotero_write_common.py` | `ZoteroWriteError` + LOGGER (leaf) |
| `pdf.py` · `pdf_fetch.py` | extract text from a local PDF; fetch OA PDFs (size/timeout caps) |
| `llm.py` | `LLMClient` protocol + `InstrumentedLLMClient` (logging wrapper for OpenAI-compatible clients) |
| `llm_anthropic.py` | `AnthropicLLMClient`: native Anthropic messages-API client implementing the same `LLMClient` protocol (`.prompt` / `.pydantic_prompt`). Lazy `import anthropic`. |
| `llm_models.py` | List a provider's available model ids for the Settings picker: `list_openai_models` (httpx `GET {base_url}/models`) · `list_anthropic_models` (SDK `models.list`). Read-only; key passed in already resolved. |
| `openalex.py` · `openalex_cache.py` | prestige lookups + their SQLite cache. `OpenAlexClient(allow_network=False)` is cache-only (a miss → None, no HTTP) for interactive request paths that must not block on a search |
| `unpaywall.py` | DOI → open-access PDF URL |

**Boundaries:** must NOT import `services/` or `api/` (enforced by pre-commit).
Adapters are leaves — they depend only on stdlib, third-party clients, and
`models`/`domain`.
