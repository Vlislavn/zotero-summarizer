# Zotero Summarizer

A local-first Zotero reading assistant: collect RSS papers, rank them against
your research goals, read source-grounded reviews and train relevance from your
own labels. Zotero remains the citation/PDF library; the app owns triage state
and the history of your decisions.

```text
RSS → relevance gate → optional AI review → daily slate → you read / label / Add
          ↑                                                │
          └──────────────── retrain on labels ──────────────┘
```

## Quickstart

Requires Python 3.10+, [uv](https://docs.astral.sh/uv/getting-started/installation/)
and Node 20.19+ for the browser build. Zotero desktop is optional for app-owned
RSS triage but needed for native library/PDF workflows. An LLM endpoint is
optional; hosted and explicitly configured local providers are supported.
Local model memory requirements depend on the model and context, not just the app.

```bash
uv sync
npm ci --prefix frontend
npm run build --prefix frontend
uv run zotero-summarizer serve
```

Open <http://127.0.0.1:8000/>. First launch creates missing configuration and
opens Setup: choose AI or ML-only operation, connect Zotero where needed, and
set your research goals. Add RSS subscriptions in Settings → Sources, refresh
them, then run **Today → Triage backlog**. For headless setup use
`uv run zotero-summarizer setup`.

Saving settings is not verification. Local-model Doctor/calibration checks can
refuse with **unknown** when effective runtime resources are not established;
model inventory or free RAM is not a safety certificate. See
[setup behavior](docs/features.md#setup-and-configuration).

## Find your workflow

| You want to… | Start here |
|---|---|
| Cull feed papers | [Daily triage](docs/features.md#daily-triage) |
| Read a paper and ask questions | [Reading reviews](docs/features.md#reading-reviews), [questions and notes](docs/features.md#paper-questions-and-notes) |
| Find papers on a topic | [Search](docs/features.md#search) |
| Find related or new papers from a saved seed | [Related saved papers](docs/features.md#related-saved-papers), [discovery](docs/features.md#saved-paper-discovery) |
| Label papers and improve relevance | [Labels and learning](docs/features.md#labels-and-learning) |
| Browse saved weekly reports | [Weekly digests](docs/features.md#weekly-digests) |
| Work disconnected or from another device | [Offline review](docs/features.md#offline-review), [remote access](docs/features.md#remote-access) |
| Understand native writes and recovery | [Zotero changes](docs/features.md#zotero-changes), [read-state backups](docs/features.md#rss-read-state-backups) |

## Configuration and privacy

Setup creates `goals.yaml` and `.env` only when absent. Edit intent and model
routes through Setup/Settings; app state defaults to gitignored `data/`.
Provider secrets may be resolved from a named environment variable or the OS
keyring. Do not commit private configuration, account sessions, library exports
or model/evaluation artifacts.

Local-first does not mean every operation is offline: configured hosted models
and public-source acquisition can transmit their declared inputs. Private
comment-context experiments require their own consent. Ordinary `serve` stays
loopback-only; do not expose it through an unauthenticated LAN shortcut.

## Documentation

```text
docs/architecture.md  system structure, dependencies and data ownership
docs/features.md      one current behavior paragraph per feature, linked to code/tests
docs/overrides.md     generated environment-variable reference
```

- [Contributing](CONTRIBUTING.md) — development setup, checks and PR publication.
- [Tests](tests/README.md) — isolation, opt-in integration checks and failure diagnosis.
- Package READMEs — local implementation contracts beside the code.
- [Changelog](CHANGELOG.md) — change history, not current operating instructions.

Use `uv run zotero-summarizer --help` and a command's `--help` for its current
arguments. For code changes, follow [CONTRIBUTING.md](CONTRIBUTING.md); checks
alone are not evidence of a real feature action or production acceptance.
