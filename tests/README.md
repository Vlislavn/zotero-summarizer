# Tests

Follow the isolated test/runtime rules in [architecture](../docs/architecture.md).
`test_calibration_load_admission.py` runs real calibration and factory admission
against inert lower adapters: local and cached refusal, both constructors,
HTTP job status, hosted calibration and ordinary local application controls.
`test_setup_attempt_regressions.py` covers inventory cancellation, actual callback
exclusion past executor shutdown, active/interrupted attempt identity, external
credential rotation, empty retries and malformed endpoint failure data.
No live inference, credentials, network, private library or hardware load occurs.
