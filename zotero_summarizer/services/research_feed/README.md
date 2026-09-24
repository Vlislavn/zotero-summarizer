# services/research_feed — weekly Research Intelligence

One bounded command projects the existing app RSS and current deep-review
artifacts into a project-specific engineering digest. It adds no crawler, PDF
extractor, or model pipeline.

```text
rss_items → source/dedupe → prior triage → budget → existing deep_review
          → engineering card → data/research_feed/weekly-<run-id>/{*.json,*.md}
                                      └─ optional reviewed Zotero tag queue
```

| file | responsibility |
|---|---|
| `profile.py` | Versioned user-editable profile and controlled topic taxonomy under `data/research_feed/profile.json`. |
| `source.py` | Bounded date-range adapter over app-owned RSS plus DOI/source/title dedupe. |
| `runner.py` | Budgeted triage, optional missing deep reviews through `library.deep_review`, per-paper failure isolation, metrics/watermark, and dry-run/idempotent writeback. Tag idempotency looks up pending changes by target Zotero item, so unrelated older queue rows cannot push a matching writeback beyond a global scan cap. |
| `card.py` | Pure engineering-card projection; only exact validated artifact URLs survive. |
| `render.py` | Canonical JSON and compact Markdown persistence. |

Run weekly: `uv run zotero-summarizer research-feed run --from 2026-08-22
--to 2026-08-29`. Add `--venue NeurIPS` for conference mode, `--cached-only`
to avoid new model work, or `--queue-zotero` to opt into reviewable tags. A
cron/launchd entry can invoke the same command weekly. Edit the generated
`profile.json` to add a theme/project. Each report name includes a deterministic
fingerprint of its date window, venue, profile, and output-affecting run options,
and a run suffix so repeated executions retain immutable results. JSON and Markdown
are staged together and published by one same-filesystem directory rename; the root
`state.json` pointer is atomically updated only after publication. A state-write
failure leaves the prior pointer and complete bundle intact. Independent runs ending
on the same date keep separate artifacts. Reversed date windows fail before
profile/source reads or writes. The sole RSS adapter is `load_candidates`;
the unused source protocol and state-only class wrapper are removed.

Offline acceptance is `uv run python tools/eval_research_feed.py --check`.
The shipped 30-paper fixture records human inclusion and verified artifact labels
but lacks frozen pre-human abstracts/scores/summaries and review outputs. The
checker exits nonzero and reports unavailable (`null`) production metrics,
not fallback-path zero precision or by-construction code-link precision. The
separate 17-paper fixture supplies a clearly labelled reading-policy diagnostic.
See `docs/issue-evidence-research-feed.md` for the required source snapshot;
ARE's deterministic hard-check principle is used, but event-trajectory judging
is deferred because this is a fixed paper projection, not an agent trace.

Engineering cards preserve a withheld reading action as `worth_reading="unknown"`
and include current/stored reading-policy flags in `evidence_gaps`; absence is not
converted to skip. The existing JSON/Markdown projection and optional tag writer
carry this state rather than treating a failed goal assessment as a negative result.

CLI and direct `run_weekly` calls share strict work-budget admission before
profile/source I/O: shortlist 1–100, cards 1–20, source rows 1–5,000, review wait
1–86,400 seconds. Omitted shortlist/card budgets use the saved profile; zero,
booleans and fractional counts are invalid. These are one-shot operational caps,
not measured model capacities. A short wait is used exactly, not raised to 30s;
it does not cancel an already running review job (outcome handling remains A170).
Future profile schema versions are rejected without modifying the stored file.
Cached-only CLI projection skips app startup entirely; it needs existing RSS and
optional review artifacts, not a goals file, classifier or provider initialization.
Generation uses the existing no-background startup mode: no prewarm, server-job
recovery, classifier retraining or slate rescoring. Enabled model prerequisites
must already be prepared for generation; they do not apply to cached-only reads.

Source admission is newest-first (row ID breaks equal-time ties), filters venue
case-insensitively, then streams the existing DOI/source/title deduplication before
the unique-result cap. Earlier unrelated or duplicate rows cannot consume the
budget. `discovered`/`deduplicated` count admitted unique matching papers, not all
raw rows in the date window. Cursor consumption stops at the cap; sorting/scanning
the underlying window can still require database work. Malformed nonempty source
timestamps raise instead of silently becoming absent dates.

**Boundary:** may compose library services and storage reads. Zotero writes are
optional pending actions only; default runs are dry-run.
