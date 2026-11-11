# Tests

How to run the suite, what isolates it, which checks are opt-in and how to
diagnose failures. Per-test behavior lives in each module's docstrings; feature
behavior is mapped to code and tests in [features](../docs/features.md).

## Run

```bash
pytest -q tests/test_x.py          # focused (add -k NAME, -x as needed)
make test                          # full Python suite: pytest -q --forked
make check                         # lint + tests + frontend lint/test/build + smoke test
cd frontend && npm test            # frontend unit tests
```

`make check` runs the tests with `KMP_DUPLICATE_LIB_OK=TRUE` and builds the
frontend into a disposable directory, leaving `frontend/dist` untouched. Setup,
the check/publish flow and PR rules: [CONTRIBUTING.md](../CONTRIBUTING.md#check-and-publish).

## Isolation

[`conftest.py`](conftest.py) isolates every ordinary test:

| Boundary | Behavior |
|---|---|
| Project | A session-owned temporary project keeps collection from loading the real `.env`; each test gets its own `ZOTERO_SUMMARIZER_HOME` (`tmp_path`) |
| Runtime | The process-global application context is reset per test |
| Credentials | `*_API_KEY` / `*_TOKEN` variables and OS-keyring reads are hidden |
| Network | IPv4/IPv6 `connect` raises unless an owned integration fixture replaces the guard |
| Models | `onprem` is stubbed and the corpus encoder is a test double; other integrations are controlled per test |
| Logging | The app logger's level/flags/handlers are restored; only newly owned, unshared handlers are closed ([`test_app_logger_isolation.py`](test_app_logger_isolation.py)) |
| Temp files | `PYTEST_DEBUG_TEMPROOT` points at the session's directory, so forked tests do not rescan the shared pytest backlog and the parent removes its own tree; an explicit `--basetemp` wins |
| Proxy | `no_proxy=*` avoids macOS SystemConfiguration proxy lookup after `fork()`; it is not an application setting |

Rules for tests:

- Use temporary SQLite/data and scripted clients; never a personal Zotero
  database, provider account, private library or local model weights.
- Shared owned fixtures: `_generated_write_fixtures.py`, `_search_evidence_fixtures.py`
  (Search question-evidence assembly with the real parser/grounding/store),
  `_zotero_fixtures.py`, `_gateway_process.py` (below).
- Do not set `HF_HUB_OFFLINE`: it also changes RSS/PDF/search acquisition policy,
  not only model downloads. Use `ZS_OFFLINE` where the app contract needs it.
- This is not an OS sandbox. Production code never imports test helpers.

## Opt-in suites

Default runs skip these (a skip is not coverage). They need installed
dependencies and an explicitly owned environment; run resource-heavy ones in the
foreground, one at a time, with memory headroom. Flags are not an authorization
or resource guard.

| Suite | Enable |
|---|---|
| Built-app Chromium (review reading, interpretation visual, connection, discovery, research feed, setup, Today and others) | `npm run build`, then `ZS_APPLICATION_BROWSER_SMOKE=1 pytest -q tests/test_<name>_browser.py` |
| Connection/settings browser journeys | also `CONNECTION_BROWSER_DIST=<disposable build dir>` (needs `index.html`; never the main `frontend/dist`) |
| Offline-review browser | `ZS_OFFLINE_REVIEW_BROWSER_SMOKE=1`, `ZS_OFFLINE_REVIEW_BROWSER_SYNTHETIC=1`, `ZS_OFFLINE_REVIEW_BROWSER_SLOT=reserved`; installed Chrome |
| Review-question browser | `ZS_REVIEW_QUESTION_BROWSER_SMOKE=1`, `…_SYNTHETIC=1`, `…_SLOT=reserved` |
| Local Chromium egress/article | `ZS_BROWSER_EGRESS_SMOKE=1` |
| Frozen real-review replay | `ZS_REVIEW_ACCEPTANCE_MANIFEST=<private manifest>`; missing manifest skips, no implicit private library |
| Real gateway process/transport | no flag; owned POSIX sockets/Uvicorn via `_gateway_process.py` (`owned_server`, `wire`, `get`); Vite cases need Node and frontend dependencies |
| Vite header contract | `ZS_VITE_PROXY_CONTRACT_SMOKE=1`, `ZS_VITE_PROXY_TEST_NODE=<absolute node path>` |
| Native SDK wire (26 cases) | `python tests/setup_request_transport_bootstrap.py --artifacts <new dir under data/issue129-validation>`; it runs [`test_setup_request_transport.py`](test_setup_request_transport.py) in an isolated interpreter |

Browser runs launch one owned listener, profile, SQLite store and artifact tree
under `data/` (`ZS_REVIEW_BROWSER_ARTIFACT_ROOT` selects another directory; its
files may be private); do not run two browser-slot owners concurrently. Browser
suites that serve a build must use a build made for that run, not whatever
`frontend/dist` holds.

Timing budgets have a default, a hard maximum and an override; values above the
maximum are rejected.

| Variable | Default / max |
|---|---|
| `ZS_TEST_MAKE_TIMEOUT_SECONDS` | 10 / 60 (`make` launcher tests) |
| `ZS_TEST_GIT_TIMEOUT_SECONDS` | 10 / 60 |
| `ZS_TEST_BARRIER_CAP_SECONDS` | 5 / 30 (concurrency barriers) |
| `ZS_FEEDBACK_SCHEDULER_TEST_CAP_SECONDS` | ≤ 60 |
| `ZS_GATEWAY_TEST_READY_STEPS`, `ZS_GATEWAY_TEST_SAFETY_SECONDS` | 200 (≤ 600), 15 (≤ 60) |
| `ZS_TEST_SETUP_NATIVE_SDK_TIMEOUT_SECONDS` | 120 / 120 |
| `ZS_TEST_SETUP_NATIVE_TIMING_SECONDS` | 1 / 5 |
| `ZS_QUESTION_BROWSER_WAIT_SECONDS`, `ZS_OFFLINE_REVIEW_BROWSER_WAIT_SECONDS` | 30 |

## Evidence ladder

```text
ordinary tests → temporary app state + scripted integrations → machine contract
opt-in suites  → owned browser / process / transport          → scoped integration
real usage     → separately authorized environment            → feature / native / user acceptance
```

Synthetic fixtures, headers and transports are wiring checks, not proof of model
quality, scientific usefulness, hardware safety, TLS/device policy, native-editor
behavior or private-library persistence. Report the command, revision/environment
and observed result, and keep focused runs, full gates, unrun cases, skips and real
feature actions distinct. Missing judgments stay unmeasured.

## Troubleshooting

- **macOS native-library fork crash** (LightGBM/torch/OpenMP): `--forked` contains a
  crashing child but does not turn it into a pass. Duplicate-runtime aborts
  (`OMP: Error #15`) are worked around with `KMP_DUPLICATE_LIB_OK=TRUE`, as
  `make check` does; this is a test-environment workaround, not an application fix.
- **Compare failures with a baseline run; do not expect zero.** Run the same command on a
  clean checkout, diff the failed sets, then rerun touched files alone. Record
  each failure as reproduced, environmental or unexplained. A nonzero complete gate
  still blocks the checked PR publisher.
- **Unexpected skips:** read the skip reason; opt-in prerequisites above are the usual cause.
- **Network assertion** ("Tests must mock network connections"): an unowned test
  reached a socket; inject a transport or use an owned fixture.
- **Docs checks:** [`test_documentation.py`](test_documentation.py) validates the
  doc inventory, local links/anchors and feature-paragraph structure;
  [`test_config_overrides.py`](test_config_overrides.py) compares the override
  reference with its generator. Both are structural, not truth checks of prose.
