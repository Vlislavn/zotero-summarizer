# services/golden/relabel_audit — labeling reliability study

Blind test-retest: re-show you a stratified sample of already-labeled papers
(original verdict hidden) and measure agreement. Answers "how noisy are my own
labels?" — the ceiling on what any model can learn.

```
golden rows ─_sampling→ stratified blind sample ─_session→ JSON (data/relabel-audit-session.json)
     you re-label (blind)  ──record──> responses
     _metrics: Cohen's κ + ICC(2,1) + Pearson + Spearman
     _trickle: surface 1–2 audit cards/day (rate-limited)
```

| file | responsibility |
|---|---|
| `_constants.py` | age buckets, dataclasses (dependency-light); `PRIORITY_TO_SCORE` re-exports `domain.PRIORITY_TO_RELEVANCE` (single source); `now_iso` re-exports `services._common.now_iso_z` (de-duped) |
| `_sampling.py` | eligibility predicate + stratified sampling; golden-CSV read reuses `services._common.load_golden_rows` (de-duped) |
| `_session.py` | session JSON I/O (create/resume/record) |
| `_metrics.py` | κ / ICC / Pearson / Spearman over paired responses |
| `_trickle.py` | daily-trickle picker with the 24h rate-limit gate |
| `__init__.py` | public surface (re-exports the helpers above) |

Session creation, response recording and trickle timestamps all use the shared
`write_json_atomic` writer. Failed writes/replacements preserve the previous
session, clean their unique staging file and propagate the error. JSON is compact
UTF-8; its schema is unchanged. Publication is atomic, not a transaction spanning
read–modify–write: concurrent updates can still overwrite an earlier snapshot.
Add serialization if concurrent audit mutation must be supported; this does not
promise power-loss durability (`fsync`).
