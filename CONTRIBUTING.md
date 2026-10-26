# Contributing

## Setup
```bash
uv sync --extra dev
npm ci --prefix frontend
source .venv/bin/activate
make dev
```
Keep any installed optional extras alongside `dev` when syncing.

## Change
- Reuse code; follow [architecture](docs/architecture.md) and [guardrails](tools/precommit/README.md).
- Add regression tests; update the package README, affected [feature](docs/features.md) and [changelog](CHANGELOG.md).
- Keep private data out of Git; never stash someone else's changes.

## Check and publish
```bash
make lint  # code and package README staged, before committing
make check # complete local checks; no publication
# Commit the intended files, then:
tools/create-pr.sh 'Title' /path/to/body.md
```
The publisher requires a clean committed PR branch and checks before pushing.
Record observed feature results and limits using the [PR template](.github/pull_request_template.md).
See [tests](tests/README.md) for isolation and troubleshooting.
Update existing PRs rather than creating another.
No automatic commit hooks or hosted CI are required.
