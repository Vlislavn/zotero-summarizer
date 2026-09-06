# services/sync — offline mutation sync

The server SQLite database remains canonical. `pull` returns compact paper
snapshots plus a monotonic field-change cursor; `push` applies ordered verdict or
review-note mutations. Each mutation has a UUID and per-field base revision, so
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

SQLite triggers capture writes from every existing server surface. This is
field-level optimistic concurrency, not database replication: PDFs, annotations,
AI runs, and Zotero filesystem state stay server-only. The JSONL label trajectory
is still best-effort after the transaction; `sync_mutations` is the durable
mutation/conflict audit.

This protocol is currently safe for the default same-machine/loopback PWA only.
It has no remote-user authentication or HTTPS bootstrap; exposing it to a LAN or
internet client is deferred until that transport boundary exists. Post-commit
mirrors remain best-effort like the online route, and the JSONL transition log is
not an exactly-once transactional outbox.

Verdict `delete` effects now share the online retraction command, including UUID
replay. SQLite deletion revisions remain pending until tag removal is confirmed;
a replay consults the current label under a write transaction, not the historical
mutation value. Conflicts/rejections do not run effects. The dispatcher no longer
catches every effect exception: retraction writer failures propagate and remain
retryable; only the explicit Zotero-unconfigured local-first boundary is optional.
Only pre-commit validation/storage `ValueError`s become rejected mutations;
post-commit effect errors propagate without misreporting the durable write.
Set-label mirrors also read current state, including after materialization.
Review-note deletion and older CSV/comment enrichment contracts are unchanged.
