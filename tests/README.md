# Isolated test lifecycle

The existing autouse boundary isolates app context, credentials, network and the
named application's logger. It restores level via `setLevel`, propagation,
disabled state and original handlers, closing only new unshared handlers.
Handlers attached to root or another logger stay open. Restoration and remaining
closures run even when one close fails; that error still fails teardown.
Production logging remains unchanged.

`test_app_logger_isolation.py` copies the real conftest into ordered pytest
subprocess scenarios, keeping diagnostic state out of the outer test process.
They check cache/flag restoration, handler ownership, closure failure and warning
capture. Real `setup_logging` uses a test-owned log path, ignoring any inherited
absolute `APP_LOG_FILE`; setup failures leave the outer logger untouched.

```sh
pytest -q tests/test_app_logger_isolation.py tests/test_review_fleet.py \
  tests/test_startup_boundaries.py
```

This verifies test isolation, not live-model, Zotero or scientific behavior.
See the root architecture verification workflow.
