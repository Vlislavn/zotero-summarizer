# Tests

See [architecture verification](../docs/architecture.md#verify-a-change) for the
isolated backend environment, native-library fork caveat and repository gates.
Production code must not import test helpers. Every Python test/helper stays
within the same500physical-line cap as other Python files.

## Disconnected review

`test_sync_keep_server.py` checks no-op receipts, stale choices, full canonical
note/comment evidence beyond outbound caps, and conservative comparison after a
lost online ACK without duplicate field history or native effects.

`test_offline_review_browser.py` and `_offline_review_browser.py` exercise an
owned installed Chrome persistent profile against actual local TCP/FastAPI,
app SQLite and IndexedDB. The fixture stops/restarts only its own listener and
browser, keeps exact outbox UUID/payload observations, and checks canonical
history, immutable replay and both conflict choices. It imports no question
submission backend to obtain offline review coverage.

Opt-in requires `ZS_OFFLINE_REVIEW_BROWSER_SMOKE=1`, explicit
`ZS_OFFLINE_REVIEW_BROWSER_SYNTHETIC=1` and parent serialized-slot admission.
Run through the repository's selected foreground resource guard; environment
flags by themselves are not a memory/process/authorization guard. An installed
browser and a verified built frontend are prerequisites; no browser/model
installation or downloads are a fallback.

All databases, profiles, PDFs, logs and screenshots are owned synthetic artifacts
under data. AI/training/network sources are disabled. The retained review packet
is constructed through the existing cache/identity owner as synthetic input,
not produced or scientifically validated by a model. Connector detection is
suppressed only for the owned synthetic native database. This is not physical
iOS, remote HTTPS/revocation, private-library or real-user native-write evidence.

Frontend fake-indexeddb tests are a separate scope. Keep their async ownership,
transaction-complete ACK and negative/error assertions; they do not establish
service-worker/browser-storage behavior. Returning a mock function from a Vitest
setup hook registers a cleanup—use explicit undefined setup returns. With Vitest
fake timers, assert at explicit awaited clock/act barriers rather than leaving
an unadvanced testing-library async-wrapper timer.
