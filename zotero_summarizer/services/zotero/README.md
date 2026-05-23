# services/zotero — the write path

The only road back into Zotero. Triage never writes directly: it queues
**pending changes** that you review, then apply. Apply backs up the Zotero DB
first. Also holds read helpers for the Zotero routes and note interpretation.

```
triage/library ─queue→ pending_changes (SQLite)  ──UI review──> apply
                                                      └─ ZoteroWriter (backup → tags/notes/collections)
zotero.py      : read helpers for /api/zotero/* (items, collections, tags)
note_analyzer  : interpret user-written Zotero notes as golden labels
```

| file | responsibility |
|---|---|
| `pending.py` | build/queue/apply pending tag/note/collection changes |
| `_notes.py` | Zotero-safe note HTML builders (triage/verdict/digest) — re-exported by `pending` |
| `zotero.py` | read-side helpers + the reader/writer accessors for routes |
| `note_analyzer.py` | classify user notes into priorities for the golden set |

**Boundaries:** imports `integrations.zotero_write/read`, `corpus`; standard
services rules. (Module path is `services.zotero.zotero` — the inner module
keeps the original name.)
