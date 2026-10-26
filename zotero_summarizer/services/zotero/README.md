# services/zotero — Zotero access + optional read resolver

`build_digest_note_html(..., interpretation=record)` accepts an explicitly supplied
qualified experiment record whose digest must match the rendered digest. It reuses
Library's shared three-group projection and keeps ownership metadata as the final
paragraph. Omitted/None/qualified-disabled inputs preserve the ordinary HTML
bytes; supplied records go through Library's existing qualification/refusal path,
not a second validator. This builds HTML only; no experiment result grants
native-write consent or target/Add authority.

The only road back into Zotero. Triage never writes directly: it queues
**pending changes** that you review, then apply. Apply backs up the Zotero DB
first. Also holds read helpers for the Zotero routes, note interpretation, and
the `get_library_reader()` resolver used by read-only Library flows.

Composite My notes and verdict note/label delivery share the current-intent
owner in `mirror_notes.py`: real backup, closed positive-ID applying claims,
claim commit, current intent/target/native-plan revalidation, native partition,
terminal commit and separate domain ACK. Original lookup and selected source
identity are both bound. Every verdict ACK rechecks actual native completion,
including nonempty batches; a new native edit in that gap leaves deletion pending.
This is not atomic exclusion of independent native writers. Ambiguous completion stays applying and
blocks automatic replay. These records are history-only, not Pending approval.
Verdict retries derive actual native deltas, preserving completed components and
unrelated tags/manual notes. Deletion clears only structurally owned verdict notes;
quoting a marker in a manual note does not create ownership. Reconciliation without
a matching configured writer cannot acknowledge both components by context exit.
The raw ID-zero verdict helper entry points are removed, not aliased. Generated
digests still require their separate explicit Pending approval.

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
| `_notes.py` | Zotero-safe triage/digest/user-note HTML (canonical verdict HTML/marker live in `models.zotero_actions`, shared by generation and history admission); triage notes retain versioned delayed summaries and optional exact-URL Method & code, while digests include selective-reading action, supported technical parameters and writing-friction reasons; empty sections vanish and distinct markers keep note types idempotent. The independently generated `should_deep_read` advice is omitted to avoid a second competing verdict; the ranked priority and rationale remain visible, and the stored artifact is unchanged. `What to read` section hints remain advisory and may need human verification |
| `zotero.py` | read-side helpers + the reader/writer accessors for routes. `get_zotero_reader_or_raise` / `get_zotero_writer_or_raise` stay strict for Zotero routes and writes. `get_library_reader()` is the read-path resolver: live Zotero reader when configured, else `services.library.app_library_reader.AppLibraryReader` over kept RSS papers, so the Library queue, paper brief, ask-paper, and deep review still work without Zotero. `resolve_reader_for_key(item_key)` resolves by the KEY's shape instead: a `stable_feed_key` (`feed:<ns>:<sha>`, an un-materialized Today paper) → `AppLibraryReader` EVEN with a live Zotero reader present (only the app library resolves it, decision-independent), anything else → `get_library_reader()` — this is what lets render/detail serve an in-place-reviewed feed paper that has no Zotero item yet. `zotero_set_item_priority` uses the explicit human-editor receipt owner for `label:*` changes; committed app verdicts and My notes use the separate current-intent system-mirror owner, while direct Zotero/iPad labels reconcile back later |
| `mirror_notes.py` | shared current-intent composite/verdict mirror state machine; closed history, current-source/target revalidation and separate domain ACK |
| `note_analyzer.py` | classify user notes into priorities for the golden set |

Reject is one captured all-pending selection: the existing repository setter's
strict transaction validates every ID/status and the final affected count before
commit. Missing/stale/partially-updatable selections become409 without changing
valid siblings. Client receipt validation uses that exact count; it never treats
zero/partial/malformed200results as success. Other writer/Apply flows are unchanged.

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

Deep review supplies the shared policy-projected digest to the existing digest-note
renderer: its Quality label uses the independent scientific grade (or stays ungraded
for non-papers/failed assessment), not the retained `model_digest` self-grade. Genuine
legacy calls without an independent assessment retain their old grade. This changes
neither the writer/backup/consent boundary nor previously saved notes on read.

Digest strength and weakness paragraphs precede the unchanged version-1 quality
metadata footer. The footer is always the last meaningful paragraph, preserving
structural ownership after native editors discard HTML comments.

A completed review is published before its generated-note proposal is staged,
using that exact current publication revision under the existing cache lock.
Staging uses `generated_notes.stage_current_digest` directly; the unconsumed
compatibility digest-note entry is removed, not aliased. It is app-queue work only,
never a native-write receipt. A queue failure
preserves the independently accepted review but leaves the worker in error;
confirmation/source/target/backup and uncertain-delivery gates are unchanged.

Pending Reject commits generated audit and ordinary status changes together after validating the entire selection; a stale row or direct/mirror history refuses all selected changes.
