# Isolated test lifecycle

The existing autouse boundary isolates app context, credentials, network and the
named application's logger. It restores level via `setLevel`, propagation,
disabled state and original handlers, closing only newly attached handlers.
Production logging remains unchanged.

`test_app_logger_isolation.py` copies the real conftest into a nested ordered
pytest scenario. One test warms the DEBUG enablement cache and mutates logger
state/handlers; the next verifies exact restoration, owned closure and capture.
The outer test cleans its own diagnostic state even on failure.

```sh
pytest -q tests/test_app_logger_isolation.py tests/test_review_fleet.py \
  tests/test_startup_boundaries.py
```

This verifies test isolation, not live-model, Zotero or scientific behavior.
See the root architecture verification workflow.
