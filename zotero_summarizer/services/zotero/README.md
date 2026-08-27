# services/zotero — Zotero access + optional read resolver

The only road back into Zotero. Triage never writes directly: it queues
**pending changes** that you review, then apply. Apply backs up the Zotero DB
first. Also holds read helpers for the Zotero routes, note interpretation, and
the `get_library_reader()` resolver used by read-only Library flows.

```
triage/library ─queue→ pending_changes (SQLite)  ──UI review──> apply
                                                      └─ ZoteroWriter (backup → tags/notes/collections)
zotero.py      : /api/zotero/* helpers + reader/writer accessors
note_analyzer  : interpret user-written Zotero notes as golden labels
```

| file | responsibility |
|---|---|
| `pending.py` | `PendingChangePlanner` builds, `queue_changes_for_item` queues, `apply_pending_changes` applies pending tag/note/collection changes (`req.retry=True` re-applies FAILED rows instead of PENDING — re-attempt a failed Zotero write via the same writer path, no re-queue); tag builders — `build_label_tag_change` (`label:<band>`, the human ground truth) and `build_rel_tag_change` (`zs:rel/<band>` ML-relevance, distinct namespace). Triage no longer auto-writes a machine `zs:<priority>` tag (retired — `label:*` is the single priority namespace). The post-apply Inbox removal stays best-effort (a WARNING, never fails the apply) but its failure is now also surfaced additively in the response as `inbox_removed_error` (`str | None`, next to `inbox_removed`) so a caller isn't left guessing why the count stayed 0 |
| `_notes.py` | Zotero-safe triage/verdict/digest/user-note HTML; digest includes selective-reading action, supported technical parameters and grounded writing-friction reasons; empty sections vanish and distinct markers keep note types idempotent |
| `zotero.py` | read-side helpers + the reader/writer accessors for routes. `get_zotero_reader_or_raise` / `get_zotero_writer_or_raise` stay strict for Zotero routes and writes. `get_library_reader()` is the read-path resolver: live Zotero reader when configured, else `services.library.app_library_reader.AppLibraryReader` over kept RSS papers, so the Library queue, paper brief, ask-paper, and deep review still work without Zotero. `resolve_reader_for_key(item_key)` resolves by the KEY's shape instead: a `stable_feed_key` (`feed:<ns>:<sha>`, an un-materialized Today paper) → `AppLibraryReader` EVEN with a live Zotero reader present (only the app library resolves it, decision-independent), anything else → `get_library_reader()` — this is what lets render/detail serve an in-place-reviewed feed paper that has no Zotero item yet. `zotero_set_label_tag` mirrors the app's committed current verdict to the portable `label:<priority>` tag; a direct Zotero/iPad edit reconciles back later, while the app owns decision state/history. `zotero_upsert_user_note` directly upserts the free-text "My notes" review note under `USER_NOTE_MARKER` (refuses while Zotero is open); `zotero_set_item_priority` route writes the `label:*` tag |
| `note_analyzer.py` | classify user notes into priorities for the golden set |

**Boundaries:** imports `integrations.zotero_write/read`, `corpus`; standard
services rules. (Module path is `services.zotero.zotero` — the inner module
keeps the original name.)
