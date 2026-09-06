# services/research_feed — weekly Research Intelligence

One bounded command projects the existing app RSS and current deep-review
artifacts into a project-specific engineering digest. It adds no crawler, PDF
extractor, or model pipeline.

```text
rss_items → source/dedupe → prior triage → budget → existing deep_review
          → engineering card → data/research_feed/{weekly-*.json,weekly-*.md}
                                      └─ optional reviewed Zotero tag queue
```

| file | responsibility |
|---|---|
| `profile.py` | Versioned user-editable profile and controlled topic taxonomy under `data/research_feed/profile.json`. |
| `source.py` | Bounded date-range adapter over app-owned RSS plus DOI/source/title dedupe. |
| `runner.py` | Budgeted triage, optional missing deep reviews through `library.deep_review`, per-paper failure isolation, metrics/watermark, and dry-run/idempotent writeback. |
| `card.py` | Pure engineering-card projection; only exact validated artifact URLs survive. |
| `render.py` | Canonical JSON and compact Markdown persistence. |

Run weekly: `uv run zotero-summarizer research-feed run --from 2026-08-22
--to 2026-08-29`. Add `--venue NeurIPS` for conference mode, `--cached-only`
to avoid new model work, or `--queue-zotero` to opt into reviewable tags. A
cron/launchd entry can invoke the same command weekly. Edit the generated
`profile.json` to add a theme/project; a new source implements the bounded
candidate load contract and stays outside card logic.

Offline acceptance is `uv run python tools/eval_research_feed.py --check`.
The shipped 30-paper fixture records user inclusion labels and manually verified
artifact URLs; the separate 17-paper fixture supplies read/skim/skip agreement.

**Boundary:** may compose library services and storage reads. Zotero writes are
optional pending actions only; default runs are dry-run.
