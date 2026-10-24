# Architecture

This page explains the product loop, runtime boundaries and data ownership.
For changing and checking code use [CONTRIBUTING.md](../CONTRIBUTING.md);
for observable behavior use the [feature map](features.md).

## Product loop

```text
RSS → triage → ML gate → LLM review → daily slate → human decision
                            │                           │
                            └→ paper brief / Q&A        ├→ labels → retrain gate
                                                       └→ approved Zotero changes
```

The app fetches RSS into its own pool. The relevance gate scores candidates
before optional LLM work; Today presents papers for culling, Library supports
reading, and labels supply the training data. Search is the separate query-driven
entry point. Weekly browses saved research-feed reports, not a second ingestion
system.

Zotero remains the citation/PDF library. Adding papers and applying proposed
notes/tags require explicit user actions; generated text is not write consent.
Native writes use the backup boundary. RSS read-state synchronization is a
separate maintenance path, also backed up before mutation. See
[write and recovery rules](features.md#rss-read-state-backups).

## Runtime boundaries

```text
React UI ────────────────→ FastAPI → domain services → storage → SQLite
HTTP MCP client ────────────┘              │
CLI / feed daemon ────────────────────────┘
                                         └→ integrations → Zotero / sources / models
```

The UI and MCP client call the HTTP API. Routes translate requests and responses;
domain services own workflows. CLI commands and the optional feed daemon reuse
those services rather than running a second triage engine.

Triage can be requested by **Today → Triage backlog**, CLI `feeds run` /
`feeds tick`, or the optional `feeds serve` daemon. The daemon can score feeds
and review the slate; it does not automatically Add a pick to Zotero.

Ordinary `serve` is loopback-only. Remote access requires explicit authenticated
HTTPS/private-Unix configuration; see the
[remote access](features.md#remote-access). Browser/device storage and
server/native acknowledgements remain distinct; see
[offline review](features.md#offline-review).

## Dependency rules

```text
api → services → storage / integrations → models · contracts · domain · settings · runtime
mcp ──HTTP──→ api
```

- `integrations/` and `storage/` never import `services/` or `api/`.
- `services/` may import only `api.errors` from the API layer, not `api.app` or
  `api.routes`. New service modules belong in a domain subpackage.
- `mcp/` imports none of those application layers; it is an HTTP client.

The [static import checker](../tools/precommit/check_import_policy.py) enforces
these import rules, including relative and nested static imports. It does not
prove the absence of indirect effects through callbacks or dynamic imports.

## Code map

Paths below are relative to [`zotero_summarizer/`](../zotero_summarizer/README.md).
Each package's README owns its detailed implementation contract.

| Responsibility | Owner |
|---|---|
| HTTP routes and request/response wiring | [`api/`](../zotero_summarizer/api/README.md) and [`api/routes/`](../zotero_summarizer/api/routes/README.md) |
| Command parsing and selected-project dispatch | [`cli/`](../zotero_summarizer/cli/README.md) |
| Relevance gate, scoring and training | [`services/model/`](../zotero_summarizer/services/model/README.md) |
| Human labels, feedback and golden data | [`services/golden/`](../zotero_summarizer/services/golden/README.md) |
| RSS, summarization and daily selection | [`services/triage/`](../zotero_summarizer/services/triage/README.md) |
| Reading, full-text review, paper brief and Q&A | [`services/library/`](../zotero_summarizer/services/library/README.md) |
| Query-driven Search and explicit Add recovery | [`services/search/`](../zotero_summarizer/services/search/README.md) |
| Weekly research-feed reports | [`services/research_feed/`](../zotero_summarizer/services/research_feed/README.md) |
| Proposed/native Zotero changes | [`services/zotero/`](../zotero_summarizer/services/zotero/README.md) |
| Device snapshots, mutation receipts and conflicts | [`services/sync/`](../zotero_summarizer/services/sync/README.md) |
| Onboarding and readiness | [`services/setup/`](../zotero_summarizer/services/setup/README.md) |
| Provider routing and credentials | [`services/llm/`](../zotero_summarizer/services/llm/README.md) |
| Grounding benchmark reused by paper Q&A | [`services/faithbench/`](../zotero_summarizer/services/faithbench/README.md) |
| SQLite persistence and migrations | [`storage/`](../zotero_summarizer/storage/README.md) |
| Zotero/PDF/source/model adapters | [`integrations/`](../zotero_summarizer/integrations/README.md) |
| HTTP tool client | [`mcp/`](../zotero_summarizer/mcp/README.md) |

[`services/lifecycle.py`](../zotero_summarizer/services/lifecycle.py) is the
startup composition root; process-wide resources live on
[`RuntimeState`](../zotero_summarizer/runtime.py). The
[services overview](../zotero_summarizer/services/README.md) describes shared
infrastructure and domain connections. The running API exposes OpenAPI at `/docs`.

## Data and configuration

```text
selected project root
├─ goals.yaml   user intent and configured model routes
├─ .env         paths and environment configuration; secrets may use OS keyring
└─ data/        app databases, labels, models, caches, reports and logs
```

[`Settings`](../zotero_summarizer/settings.py) resolves paths from the selected
project. App state defaults to `data/`, including `triage_history.db`,
`corpus_cache.db`, golden CSV/JSONL, model artifacts, search sessions and the
append-only interaction log. These files and private configuration are gitignored.
Use Settings paths rather than inventing a second storage root.

Paper-read HTML, its audit, figures and explicitly consented source artifacts
are a documented exception: they live beside the Zotero PDF; their state record
lives under `data/paper_render/`. See the
[library contract](../zotero_summarizer/services/library/README.md).
Explicit path overrides, such as an absolute `APP_LOG_FILE`, can also select an
external location; `data/` is the default, not a filesystem sandbox.

First `serve` or `setup` creates missing `goals.yaml` and `.env` files and runs
versioned database migrations without overwriting existing configuration. No
manual template copying is needed. Edit intent/routing through Setup or Settings;
technical defaults and environment overrides are documented in
[setup](features.md#setup-and-configuration) and [overrides](overrides.md).
Schema changes belong in the numbered
[migration owner](../zotero_summarizer/storage/migrations.py).

## Review evidence boundary

Deep-review generation may use map-reduce notes, but claim verification uses
the original paper, including correction and fallback. Empty sources or chunk
summaries fail publication rather than becoming scientific-quality judgments.
Retained literal-source checks are not proof of scientific truth or human
usefulness. The [library README](../zotero_summarizer/services/library/README.md)
owns the implementation contract, including the internal read-only verification
audit helper; it has no public HTTP/CLI entry.

## Further reading

[Features](features.md) describes current behavior; [overrides](overrides.md) is
generated from configuration code. Developer checks and PR publication live in
[CONTRIBUTING.md](../CONTRIBUTING.md); fixture isolation and native-library
troubleshooting live in [tests/README.md](../tests/README.md).
