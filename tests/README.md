# Tests

Use [CONTRIBUTING.md](../CONTRIBUTING.md#check-and-publish) for development setup,
focused checks, the complete local gate and PR publication. The
[feature map](../docs/features.md) links current behavior to implementation owners
and representative tests. This page explains the test environment, not feature
history or the status of old PR slices.

```text
ordinary tests → temporary app state + scripted integrations → machine contracts
opt-in checks → owned browser / process / transport          → scoped integration evidence
real usage    → separately authorized environment            → feature/native/user acceptance
```

## Test environment

[`conftest.py`](conftest.py) installs the ordinary backend isolation boundary:

| Boundary | Behavior |
|---|---|
| Project selection | A session-owned temporary project prevents collection from loading the real default `.env`; each test receives its own project root |
| Runtime | The process-global application context is reset per test |
| Credentials | Provider environment keys/tokens and OS-keyring reads are hidden during a test |
| Network | IPv4/IPv6 socket connections fail unless an explicitly owned integration fixture replaces the guard |
| Models | OnPrem calls and the corpus encoder use test doubles; individual tests must explicitly control other integrations |
| Logging | The named app logger's level, flags and original handlers are restored; only newly owned unshared handlers are closed |

Do not point fixtures at a personal Zotero database, provider account or local
model. Use temporary native SQLite and scripted clients for machine contracts.
The test boundary is not an OS sandbox, and an opt-in real transport fixture has
a different, explicitly owned scope.

Logger restoration uses `setLevel` so cached enablement decisions cannot leak
between tests. Handlers shared with root/other loggers remain open; restoration
and other closures still run if one close raises, and that error fails teardown.
Production file logging is unchanged. The focused regression owner is
[`test_app_logger_isolation.py`](test_app_logger_isolation.py).

## Forks, proxy discovery and temporary files

Before collection/fork, the session sets `no_proxy=*` to avoid macOS
SystemConfiguration proxy discovery from HTTP clients after `fork()`. This is a
test setting, not an application proxy override or a fix for native ML crashes.
See [Python's proxy-discovery warning](https://docs.python.org/3.13/library/urllib.request.html).

The session also points `PYTEST_DEBUG_TEMPROOT` at its owned temporary directory.
Forked tests therefore do not rescan the shared global pytest backlog, parallel
runs keep separate roots, and the parent removes its own temporary tree at
cleanup. An explicit `--basetemp` takes precedence. No unrelated temporary files
are deleted.

`pytest --forked` contains a crashing native child; it does not turn a crash into
a pass. For a suspected macOS LightGBM/torch issue, retain the failed output,
compare the failed set with the same clean baseline, then rerun touched files in
isolation. Record whether a failure is reproduced, environmental or still
unexplained. A nonzero complete gate still blocks the checked PR publisher.

## Browser and transport checks

Ordinary checks must not install a browser, download model weights, authenticate
an account or activate a remote deployment as a fallback. Opt-in checks require
installed dependencies and an explicitly selected, owned environment. Run
resource-heavy checks foreground and serially with enough memory headroom;
environment flags alone are not a resource or authorization guard.

- [`test_review_reading_browser.py`](test_review_reading_browser.py) and
  [`test_interpretation_experiment_visual.py`](test_interpretation_experiment_visual.py)
  use `ZS_APPLICATION_BROWSER_SMOKE=1` and installed Chromium. Their fixtures
  define whether they need a built frontend or only generated HTML.
- [`test_offline_review_browser.py`](test_offline_review_browser.py) additionally
  requires `ZS_OFFLINE_REVIEW_BROWSER_SMOKE=1`,
  `ZS_OFFLINE_REVIEW_BROWSER_SYNTHETIC=1` and a parent-reserved
  `ZS_OFFLINE_REVIEW_BROWSER_SLOT=reserved`, an installed Chrome and a built
  frontend. It owns its TCP listener, profile, temporary SQLite, outbox and
  artifacts; do not run it concurrently with another browser-slot owner.
- Frozen real-review replays require their explicitly selected
  `ZS_REVIEW_ACCEPTANCE_MANIFEST`. A missing required manifest is a skip, not
  passing acceptance; never substitute a private library implicitly.
- Gateway transport cases use owned POSIX sockets/processes and synthetic
  identity headers. Vite integration needs installed Node/frontend dependencies;
  a prerequisite skip is not successful integration coverage.

Browser screenshots/geometry, fake IndexedDB, real browser storage and physical
iOS are separate kinds of evidence. A synthetic HTML wrapper is not Zotero's
native renderer. Local socket/header tests do not certify an installed trusted
TLS gateway, device policy or revocation. Model-free fixtures do not establish
scientific accuracy, recommendation usefulness or private-library persistence.

## Documentation checks

[`test_documentation.py`](test_documentation.py) keeps `docs/` to architecture,
features and generated overrides, verifies maintained local file/anchor links,
and checks task groups with one paragraph per feature. [`test_config_overrides.py`](test_config_overrides.py) separately compares
the override reference with its code generator. These are structural contracts,
not keyword-based judgments of whether feature prose is true. Review the linked
implementation and behavioral tests when changing a feature.

## Report evidence honestly

Report the actual command, revision/environment and observed result. Distinguish
focused tests, full gates, authored/unrun cases, skips and real feature actions.
Synthetic perfect scores are wiring checks, not model-quality measurements;
missing judgments stay unmeasured. Production code must not import test helpers.
For feature-specific fixture details, read the executable tests linked from
[features](../docs/features.md) and the owning package README rather than adding
another coverage ledger here.
