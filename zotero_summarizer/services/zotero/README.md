# services/zotero — Zotero access + optional read resolver

`mirror_notes.py` owns composite My notes: one connection/process lock, committed
claim, target revalidation, native terminal commit then separate domain ACK. Matched
applied history permits ACK-only continuation. Source candidate; runtime/recovery
remain unqualified.

The only road back into Zotero. Triage never writes directly: it queues
**pending changes** that you review, then apply. Apply backs up the Zotero DB
first. Also holds read helpers for the Zotero routes, note interpretation, and
the `get_library_reader()` resolver used by read-only Library flows.

Verdict notes and label tags retain `_apply_mirror_change`:
connector check, backup, apply and explicit failure propagation. It retains the
producer's `expected_io` classification when converting a failed row to
`ZoteroWriteError`. Connector refusal is explicit expected IO; absent/malformed
classification defaults to terminal, not optional pending. Queued invalid rows
remain isolated failures, while generated mirror payload defects propagate to the
caller. No note body is added to diagnostics or logs. Owned upserts retain child identity,
but can touch native clocks; Pending local-finalization uncertainty stays applying,
not retryable pending. A trashed note is replaced with a visible note.

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
| `pending.py` | `PendingChangePlanner` and `plan_changes_for_item` build plans, `apply_pending_changes` applies pending tag/note/collection changes (`req.retry=True` re-applies FAILED rows instead of PENDING — a successful retry transitions `failed → applied`, while another failure refreshes the failed error); tag builders — `build_label_tag_change` (`label:<band>`, the human ground truth) and `build_rel_tag_change` (`zs:rel/<band>` ML-relevance, distinct namespace). Triage no longer auto-writes a machine `zs:<priority>` tag. No implicit Inbox removal follows Apply; explicitly queued membership changes remain available |
| `generated_notes.py` | Closed generated digest-note staging and publication/source/body/target validation on the existing queue. No generated note is written during review. |
| `pending_confirmation.py` | Common typed presentation and whole-selection validation, one durable claim/backup/native batch, matching finalization and backup-only uncertain recovery for ordinary/generated rows; shared uncapped overlap refusal and direct-history exclusion. |
| `direct_actions.py` | Three typed human tag/priority/collection owners over existing SQL history, target/prepared-backup binding, complete native partition validation and atomic terminal audit; never Pending approval authority. |
| `mirror_notes.py` | Composite My notes on the existing projection connection: durable claims, exact intent/target admission, native terminal commit then separate domain ACK; proof-bound ACK-only continuation, never Pending approval. |
| `_notes.py` | Zotero-safe triage/verdict/digest/user-note HTML; triage notes retain versioned delayed summaries and optional exact-URL Method & code, while digests include selective-reading action, supported technical parameters and writing-friction reasons; empty sections vanish and distinct markers keep note types idempotent. The independently generated `should_deep_read` advice is omitted to avoid a second competing verdict; the ranked priority and rationale remain visible, and the stored artifact is unchanged. `What to read` section hints remain advisory and may need human verification |
| `zotero.py` | read-side helpers + the reader/writer accessors for routes. `get_zotero_reader_or_raise` / `get_zotero_writer_or_raise` stay strict for Zotero routes and writes. `get_library_reader()` is the read-path resolver: live Zotero reader when configured, else `services.library.app_library_reader.AppLibraryReader` over kept RSS papers, so the Library queue, paper brief, ask-paper, and deep review still work without Zotero. `resolve_reader_for_key(item_key)` resolves by the KEY's shape instead: a `stable_feed_key` (`feed:<ns>:<sha>`, an un-materialized Today paper) → `AppLibraryReader` EVEN with a live Zotero reader present (only the app library resolves it, decision-independent), anything else → `get_library_reader()` — this is what lets render/detail serve an in-place-reviewed feed paper that has no Zotero item yet. `zotero_set_label_tag` mirrors the app's committed current verdict to the portable `label:<priority>` tag; a direct Zotero/iPad edit reconciles back later, while the app owns decision state/history. composite My notes delivery belongs to `mirror_notes`, not an id0 wrapper; `zotero_set_item_priority` route writes the `label:*` tag |
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

## Explicit human editors — backend candidate

The tag, manual-priority and collection routes each run one synchronous owner via
`asyncio.to_thread`. The same existing process RLock spans target/uncertainty
admission, prepared backup issuance, SQL claim commit, native reference consumption,
terminal commit and connection cleanup. Its no-publication branch resolves no
review cache path, settings or FileLock. Corpus/detail follow-up runs afterward.
The typed request is consent; these editors do not invent a Pending approval hash.

`pending_actions` records positive-ID applying rows and first `direct_requested`
audit atomically on the ContextVar-selected queue, with the actual target, request
and backup-only locator. Uncapped same-item applying history is compared by stored
actual target across all action types, including Pending, before matching-state
no-ops or backup. Unknown/malformed targets block; known different targets/items do
not globally freeze delivery. All unchanged claims and the complete native partition
must validate before atomic `direct_applied`/`direct_failed` terminal events.

Success preserves old fields and adds `action_ids`, `backup_path`, and
`delivery_status`. Complete failures report the real failed/partially-applied
partition with500; unknown delivery/finalization reports409 without native retry.
Post-native corpus/detail failure is separately500 `zotero_refresh_failed`, retaining
durable native status. Rolled-back terminal updates leave applying; terminal commit
acknowledgement loss after a real commit remains unqualified, not an always-applying
or exactly-once promise.

Direct receipts are history-only in every status/writer condition: null approval
preview and no Pending Apply/retry/force/edit/reject/reset authority, even in mixed
selections. Their stored target/payload/audit/backup remain visible. UI-specific
no-action/history labelling is deferred. Backup is not inverse/restore, attachment
recovery or permanent retention. Adapters, schema and other writer families are
unchanged. This source candidate still needs guarded runtime and independent review;
full #7/native-editor/real-library/human/scientific acceptance remains open.

## Common Pending confirmation

Every Apply/retry supplies `PendingChangeApplyRequest.previews`, exactly the displayed
common `approval_preview.preview_sha256` for each selected ID. The version1 envelope
binds ID/item/type/exact payload/edit revision/native target and original generated
identity where present. No ID-only/old inner-hash alias or fresh authority inside a
mutator exists; force is only connector access. Ordinary edits atomically append
revision events even for identical saves/ABA. Missing maps fail422; stale, missing,
wrong-target or changed-state selections fail409 before any backup/native effect.
One already-stale member refuses an entire mixed selection. The existing process
RLock spans validation, one retained prepared backup, durable applying claim commit,
one target-bound native batch and matching finalization in one worker call.
Known ordinary/generated Failed rows permit explicit current approval/retry; exact
applied replay is no-op. Without a configured writer, every ordinary approval preview is null,
including approved applied/applying history; recorded audit/target/backup history
is retained without native effects. Available-writer history keeps its recorded
target rather than rebinding. Legacy ordinary applied history without an approved
target cannot authorize replay. Implicit Inbox cleanup and its receipt counters are absent; queue an explicit
collection removal instead. Corpus refresh remains read-side post-delivery work.

## Generated digest-note confirmation

Default library review publishes its cache first, then stages one immutable
`upsert_note` in `pending_changes`. Feed and private-C reviews never auto-write;
C is not activated by stored content. The compatibility `zotero_upsert_digest_note`
entry now stages the exact published default digest instead of writing it.
Unpublished/legacy reviews cannot authorize a mutation. Queue failure is visible
in job status; the successfully published cache is retained, not rolled back.

Ops → Pending shows the complete escaped payload, item key and actual native
database/user-library target, source identity, body hash and publication UUID.
Apply supplies common outer hashes in `previews={change_id: preview_sha256}`;
the original `generated_preview` digest remains a separately checked source binding.
IDs alone, old inner hashes or `force` never approve a generated row. A different target, changed
source/review, A→B→A publication or edited payload cannot inherit approval.
Same-preview applied replays do nothing. Rejection retains its user audit event.
Generated-note approval never also removes the item from Inbox.
The native target is the selected writer's canonical database path, device/inode
and actual user-library ID, not a content hash or modification clock. Switching to
another database with the same item key cannot reuse approval. Observed file/library
replacement is rejected before backup and again at adapter dispatch; this is not an
external-Zotero-process CAS. Legacy proposals with unknown/null target are display/
reject-only; no automatic target upgrade is allowed. New proposal construction
requires a strict bound target. A missing configured writer stages nothing, emits
`zotero_unavailable` diagnostic metadata and preserves the valid published review.
The existing adapter consumes the same writer-issued, scoped precreated backup
reference and performs its normal bounded retention after commit—without a second
backup. The audit keeps that original pre-dispatch reference.

Every participating Pending batch holds the cache owner's existing process RLock
through prepared-reference consumption, native delivery and finalization/connection
cleanup. Generated/mixed also acquires its reentrant publication FileLock before
the queue writer lock. Ordinary-only branches before cache path/settings/FileLock
access. This is not containment of foreign writers, other processes or all pruning. Claim/audit/backup
reference commit as `applying` before external dispatch. A lost outcome or local
finalization failure stays **Needs reconciliation**, not retryable pending.
Confirmed per-row failures can be retried only with the same still-current preview.
There is no distributed transaction, automatic upgrade, fake undo or permanent
backup-retention promise (the native writer's existing retention still applies).
Other explicit Add/verdict/manual-note/label-mirror owners and their transaction-owned
receipts are unchanged; this component does not unify every other Zotero writer.

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

Digest notes retain every saved read/skip target in its original order, including
the fourth exact target allowed by the existing reading contract; no silent
three-target truncation remains. Escaping, ownership and write boundaries are
unchanged. This is rendering preservation, not native save/reopen acceptance.

Digest strength and weakness paragraphs precede the unchanged version-1 quality
metadata footer. The footer is always the last meaningful paragraph, preserving
structural ownership after native editors discard HTML comments.
