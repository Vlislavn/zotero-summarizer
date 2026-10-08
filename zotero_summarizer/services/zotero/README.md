# services/zotero — Zotero access + optional read resolver

The only road back into Zotero. Triage never writes directly: it queues
**pending changes** that you review, then apply. Apply backs up the Zotero DB
first. Also holds read helpers for the Zotero routes, note interpretation, and
the `get_library_reader()` resolver used by read-only Library flows.

Verdict notes, user notes, digest notes and label tags share `_apply_mirror_change`:
connector check, backup, apply and explicit failure propagation. Queue retries
deliver an identical visible note only once, including after a failed local
pending-status commit; a trashed note is replaced with a visible note.

`zotero_set_label_tag(key, None)` clears recognized human-label tags through
that same guarded, backup-first path. The shared tag builder handles absence;
there is no separate clear-label endpoint or writer. Unrelated namespaces and
unrecognized custom labels remain untouched. Invalid non-null priorities still
raise. Golden's online/offline retraction command uses this primitive with
revision-based delivery receipts; reconciliation guards and retries those intents.

`plan_changes_for_item` only builds repository rows: the triage worker saves
them atomically with its result. A planning failure therefore cannot leave a
result without the requested review queue. Standalone queue callers retain
`insert_pending_changes`; none of these planning/queueing paths writes Zotero.

```
triage/library ─queue→ pending_changes (SQLite)  ──UI review──> apply
                                                      └─ ZoteroWriter (backup → tags/notes/collections)
zotero.py      : /api/zotero/* helpers + reader/writer accessors
note_analyzer  : interpret user-written Zotero notes as golden labels
```

| file | responsibility |
|---|---|
| `pending.py` | `PendingChangePlanner` and `plan_changes_for_item` build plans, `apply_pending_changes` applies pending tag/note/collection changes (`req.retry=True` re-applies FAILED rows instead of PENDING — a successful retry transitions `failed → applied`, while another failure refreshes the failed error); tag builders — `build_label_tag_change` (`label:<band>`, the human ground truth) and `build_rel_tag_change` (`zs:rel/<band>` ML-relevance, distinct namespace). Triage no longer auto-writes a machine `zs:<priority>` tag. Post-apply Inbox removal is best-effort, waits until the paper has no pending/failed sibling changes, and surfaces `inbox_removed_error` on failure |
| `_notes.py` | Zotero-safe triage/verdict/digest/user-note HTML; triage notes retain versioned delayed summaries and optional exact-URL Method & code, while digests include selective-reading action, supported technical parameters and writing-friction reasons; empty sections vanish and distinct markers keep note types idempotent. The independently generated `should_deep_read` advice is omitted to avoid a second competing verdict; the ranked priority and rationale remain visible, and the stored artifact is unchanged. `What to read` section hints remain advisory and may need human verification |
| `zotero.py` | read-side helpers + the reader/writer accessors for routes. `get_zotero_reader_or_raise` / `get_zotero_writer_or_raise` stay strict for Zotero routes and writes. `get_library_reader()` is the read-path resolver: live Zotero reader when configured, else `services.library.app_library_reader.AppLibraryReader` over kept RSS papers, so the Library queue, paper brief, ask-paper, and deep review still work without Zotero. `resolve_reader_for_key(item_key)` resolves by the KEY's shape instead: a `stable_feed_key` (`feed:<ns>:<sha>`, an un-materialized Today paper) → `AppLibraryReader` EVEN with a live Zotero reader present (only the app library resolves it, decision-independent), anything else → `get_library_reader()` — this is what lets render/detail serve an in-place-reviewed feed paper that has no Zotero item yet. `zotero_set_label_tag` mirrors the app's committed current verdict to the portable `label:<priority>` tag; a direct Zotero/iPad edit reconciles back later, while the app owns decision state/history. `zotero_upsert_user_note` directly upserts the free-text "My notes" review note under `USER_NOTE_MARKER` (refuses while Zotero is open); `zotero_set_item_priority` route writes the `label:*` tag |
| `note_analyzer.py` | classify user notes into priorities for the golden set |

Ownership is plain text in the existing triage/digest footer and a small
verdict/user-note footer (`zs:note_type=<type>;version=<version>`). Legacy
comments remain for existing substring-based readers; comments alone are not
persistent ownership through native editor saves. Triage also retains its timestamp,
source and HTML-escaped original run ID. `include_provenance=False` suppresses both
representations. No title heuristic adopts manual notes. Removing the ownership
text loses identification; upsert replaces an identified app-owned note, including
manual edits inside it, but leaves unrelated unmarked notes untouched. Previously
stripped notes are not retroactively adopted. Comment-discard parser tests are a
wiring floor only; native save/reopen acceptance is verified separately.

Triage headlines select the first nonblank rationale or overview, including Unicode
whitespace. Optional controversies, impact, implementation and unknowns are rendered.
The raw persisted response is unchanged. By default, paper-specific text is retained
in full, including every nonblank finding, reading hint and tag; lists have no silent
six-item cap. Independent preservation tests check saved text and association loss,
with targeted corruptions rejected. These are not native-editor or scientific-truth
certification. Optional renderer keyword budgets keep a complete field or show an explicit
omission notice: they never guess a sentence boundary. Exact artifact URLs remain
intact. These opt-in budgets are not a validated 300–600-word whole-note target;
long-field omission can reduce usefulness and needs native-editor/user acceptance
before being enabled by an application caller. Shared metrics
report rendered characters, HTML characters, words, headings and actual headline fallback.

`pending.list_pending_changes` accepts an optional `item_key` filter, pushed
into the storage query before its limit. Paper-detail callers therefore retrieve
that paper's pending and historical rows independently of unrelated queue volume.

**Boundaries:** imports `integrations.zotero_write/read`, `corpus`; standard
services rules. (Module path is `services.zotero.zotero` — the inner module
keeps the original name.)

Triage provenance values (run ID and source) are URL-percent-encoded identically
in the legacy comment and visible footer. Ordinary UUIDs/readable IDs remain
unchanged; reserved punctuation, percent signs and Unicode are encoded, and
consecutive hyphens are encoded to keep HTML comments valid. Decode values with
URL unquoting, not as structural ownership markers. This prevents metadata from
impersonating another note kind; existing raw body-text marker spoofing is outside
this boundary fix. Removing the ownership footer makes a comment-stripped note
unowned rather than retroactively adopting it.

Digest strength and weakness paragraphs precede the unchanged version-1 quality
metadata footer. The footer is always the last meaningful paragraph, preserving
structural ownership after native editors discard HTML comments.
