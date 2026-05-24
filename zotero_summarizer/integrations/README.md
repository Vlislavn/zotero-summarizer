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
| `_zotero_read_items.py` · `_zotero_read_lookup.py` · `_zotero_read_feeds.py` | reader query mixins (items/detail · find/membership/tags — DOI dedup matches all `domain.normalize_doi` variants · feeds) |
| `_zotero_read_common.py` | `ZoteroReadError` + arXiv/sanitize helpers (leaf) |
| `zotero_write.py` | `ZoteroWriter`: backup + the apply-changes dispatcher |
| `_zotero_write_items.py` · `_zotero_write_tags.py` · `_zotero_write_collections.py` | writer mixins (item creation/materialization · tag/note + helpers · collections) |
| `_zotero_write_common.py` | `ZoteroWriteError` + LOGGER (leaf) |
| `pdf.py` · `pdf_fetch.py` | extract text from a local PDF; fetch OA PDFs (size/timeout caps) |
| `llm.py` | OpenAI-compatible chat client protocol |
| `openalex.py` · `openalex_cache.py` | prestige lookups + their SQLite cache |
| `unpaywall.py` | DOI → open-access PDF URL |

**Boundaries:** must NOT import `services/` or `api/` (enforced by pre-commit).
Adapters are leaves — they depend only on stdlib, third-party clients, and
`models`/`domain`.
