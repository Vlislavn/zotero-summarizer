# Contributing

## Setup
```bash
make install  # Python (with dev tools) + frontend dependencies
make dev      # backend :8000 + hot-reload UI :5173; Ctrl+C stops both
```
`make install` keeps optional extras installed earlier (for example `uv sync --extra telegram`).
`make help` lists every command.

## Change
- Reuse code; follow [architecture](docs/architecture.md) and [guardrails](tools/precommit/README.md).
- Add regression tests; update the package README, the affected [feature](docs/features.md)
  paragraph and, for significant changes, the [changelog](CHANGELOG.md).
- Keep private data out of Git; never stash someone else's changes.

## Documentation
Each fact has one home; elsewhere, link to it.

| Document | Owns | Keep out |
|---|---|---|
| [README.md](README.md) | what the app is, quick start, everyday commands, iPhone access, troubleshooting, links | feature contracts, history |
| `make help` | the command list | — |
| [docs/features.md](docs/features.md) | one short paragraph per feature: action → result → essential limit, with code/test links | implementation detail, history |
| [docs/architecture.md](docs/architecture.md) | structure, dependencies, data ownership | feature behavior |
| package `README.md` | contract for people changing that package: files, responsibilities, invariants, extension points | feature descriptions, change history, test results |
| [tests/README.md](tests/README.md) | running tests, isolation, opt-in suites, diagnosis | per-test narratives (use test docstrings) |
| [CHANGELOG.md](CHANGELOG.md) | significant behavior/API/setup changes and important fixes, ≤300 characters each | test-only or internal edits, review/qualification records, instructions |

Describe the current state in present tense ("X does Y", not "X now/no longer does Y");
history lives in the changelog and Git. Run every command you document.

## Check and publish
```bash
make lint  # guardrails + deadcode-audit gates on committed changes vs origin/main (refreshes deadcode-audit; see tools/precommit/README.md)
make check # complete local checks; no publication
# Commit the intended files, then:
tools/create-pr.sh 'Title' /path/to/body.md
```
The publisher requires a clean committed PR branch and checks before pushing.
Record observed feature results and limits using the [PR template](.github/pull_request_template.md);
the open PR's manual acceptance list is [PR208 checklist](docs/validation/pr208/manual-checklist.md).
See [tests](tests/README.md) for isolation and troubleshooting.
Update existing PRs rather than creating another.
No automatic commit hooks or hosted CI are required.
