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
| `pending.py` | `PendingChangePlanner` builds, `queue_changes_for_item` queues, `apply_pending_changes` applies pending tag/note/collection changes (`req.retry=True` re-applies FAILED rows instead of PENDING — re-attempt a failed Zotero write via the same writer path, no re-queue); tag builders — `build_label_tag_change` (`label:<band>`, the human ground truth) and `build_rel_tag_change` (`zs:rel/<band>` ML-relevance, distinct namespace). Triage no longer auto-writes a machine `zs:<priority>` tag (retired — `label:*` is the single priority namespace) |
| `_notes.py` | Zotero-safe note HTML builders (triage/verdict/digest) — re-exported by `pending` |
| `zotero.py` | read-side helpers + the reader/writer accessors for routes; `zotero_set_label_tag` (direct, instant `label:<priority>` write mirroring the verdict-note write); `zotero_set_item_priority` route writes the `label:*` tag |
| `note_analyzer.py` | classify user notes into priorities for the golden set |

**Boundaries:** imports `integrations.zotero_write/read`, `corpus`; standard
services rules. (Module path is `services.zotero.zotero` — the inner module
keeps the original name.)
