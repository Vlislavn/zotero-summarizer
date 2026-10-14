# services/sync — offline mutation sync

A *new* positive feed verdict must have a current usable review before its
SQLite mutation; an unreviewed feed mutation is rejected as `review_required`
without a label or Zotero write. An existing UUID still reaches the idempotent
replay path if its review has since gone stale. Negative verdicts and library
items do not need a feed review.

The server SQLite database remains canonical. `pull` returns compact paper
snapshots plus a monotonic field-change cursor; protocol-v1 `changes` is always
empty because clients reconcile from the snapshot and never consume the event
history. This keeps stale-device pulls independent of accumulated note/verdict
transitions. `push` applies ordered verdict/manual-note mutations or independent
immutable question creates. Each mutation has a UUID and per-field base revision, so
replay is idempotent, edits to different fields merge, and same-field divergence
returns an explicit conflict. A resolution is another mutation naming the
conflicted mutation. Delete tombstones retain their last revision, so a client
that has pulled a deletion can edit from that revision without a false conflict;
replaying a conflict UUID returns the same conflict rather than treating it as
an applied write.

An applied offline verdict then runs the same `golden.verdict_effects` command
as the online route: training-row enrichment, positive-feed materialization,
`label:*` mirror, and verdict-note mirror. Review notes share the same Zotero
mirror too. These effects are idempotent and also run for `already_applied`, so
a client retry repairs the commit→effect crash window without duplicate CSV rows
or Zotero items.

```
PWA IndexedDB queue ─push→ BEGIN IMMEDIATE: compare revision → write → remember UUID
                  ←pull─ sync_changes cursor + compact current paper snapshots
```

Question creates use `review_question/create` with `mutation_id` equal to the
canonical submission UUID and an immutable five-field value. Validation reuses
the question store's payload rules; accepted keys/text/preset IDs are not trimmed
or alias-rewritten. Only device identity is normalized. Distinct UUIDs ignore
field base revisions; exact retries (including an online-saved question) reuse
the original journal revision and add only the missing durable sync receipt.
Changed ID payloads reject without row/journal/intent writes. Receipt insertion,
question creation and staging share the existing `BEGIN IMMEDIATE` transaction.

```
review_question/create -> save/reuse question + immutable receipt -> commit
                         -> mirror_review_note(db_path, original_item_key)
pull/detail <- separate questions[] + unchanged editable manual note
```

Questions invoke exactly `golden.verdict_effects.mirror_review_note`, including
UUID replay, never `apply_verdict_effects`, QA, training or feed materialization.
The common return is exposed unchanged as `question_mirror` for questions and
`review_note_mirror` for manual-note set/delete/replay. Both contain
`{note_written, note_error, note_pending, projection_revision, target_key}` and
are derived post-commit state, not saved in immutable request/result receipts.
Durable local success stays HTTP 200 / `applied` or `already_applied`; the delivery
owner reports expected external-write/acknowledgement failures as `note_error`
with pending recovery, rather than throwing a post-save HTTP error. Unexpected
exceptions are not suppressed here. Same-UUID replay retries the current
projection without changing its original request/receipt. The delivery owner
supplies composite delivery and current acknowledgement; verdict effects remain
distinct. The online-save→sync regression verifies its pre-receipt delivery
separately from post-receipt sync/replay delivery, retaining the original
row/journal/intent identity and no-duplicate assertions.

Snapshot fields project `questions[]` to all resolved aliases and seed
question-only papers absent from the queue or manual/verdict store. Original row
keys and `submitted_at` remain intact. `revisions.review_question` uses the
existing globally ordered insertion journal; manual-note revisions stay separate.
Detail/snapshot readers perform no acknowledgement or payload normalization.

SQLite triggers capture writes from every existing server surface. This is
field-level optimistic concurrency, not database replication: PDFs, annotations,
AI runs, and Zotero filesystem state stay server-only. The JSONL label trajectory
is still best-effort after the transaction; `sync_mutations` is the durable
mutation/conflict audit.
The status `conflicts` count includes only conflict receipts without a successful
resolution mutation; resolved historical receipts remain auditable without being
reported as active work.

Snapshot review payloads are filtered through the same PDF/model/config identity
check as Library policy consumers; stale deep-review output is never synced as current.

This protocol is currently safe for the default same-machine/loopback PWA only.
It has no remote-user authentication or HTTPS bootstrap; exposing it to a LAN or
internet client is deferred until that transport boundary exists. The JSONL
transition log is not an exactly-once transactional outbox.

Verdict `delete` effects now share the online retraction command, including UUID
replay. SQLite deletion revisions remain pending until tag removal is confirmed;
a replay consults the current label under a write transaction, not the historical
mutation value. Conflicts/rejections do not run effects. The dispatcher no longer
catches every effect exception: retraction writer failures propagate and remain
retryable; only the explicit Zotero-unconfigured local-first boundary is optional.
Only pre-commit validation/storage `ValueError`s become rejected mutations;
post-commit effect errors propagate without misreporting the durable write.
Set-label mirrors also read current state, including after materialization.
Review-note set/delete and UUID replay now deliver the current body under the
same writer lock, rather than the historical mutation value. A deletion clears
the existing app-owned Zotero note body; the marked child note remains. Genuine
mirror errors propagate after the local commit, so retrying the unchanged UUID
can finish delivery without losing newer intent. Unconfigured Zotero and the
existing feed/note namespace skip remain local-first. Verdict rationales now
share the current-label writer lock too; older CSV enrichment remains separate.

Pushes contain at most 100 mutations. The optional, bounded `predecessors` UUID
list names already-applied server receipts: the next batch inherits only that
device/item/field's acknowledged revision, without rewriting immutable request
bodies or rerunning predecessor effects. Intervening writes still conflict.
Unknown or conflict receipts reject the request before writes. IndexedDB commits
these receipt IDs with acknowledgement retirement, so continuation survives a
restart or the existing 15-second whole-sync deadline. No schema or worker is added.
