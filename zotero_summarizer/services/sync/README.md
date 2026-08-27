# services/sync — offline mutation sync

The server SQLite database remains canonical. `pull` returns compact paper
snapshots plus a monotonic field-change cursor; `push` applies ordered verdict or
review-note mutations. Each mutation has a UUID and per-field base revision, so
replay is idempotent, edits to different fields merge, and same-field divergence
returns an explicit conflict. A resolution is another mutation naming the
conflicted mutation.

```
PWA IndexedDB queue ─push→ BEGIN IMMEDIATE: compare revision → write → remember UUID
                  ←pull─ sync_changes cursor + compact current paper snapshots
```

SQLite triggers capture writes from every existing server surface. This is
field-level optimistic concurrency, not database replication: PDFs, annotations,
AI runs, and Zotero filesystem state stay server-only. The JSONL label trajectory
is still best-effort after the transaction; `sync_mutations` is the durable
mutation/conflict audit.
