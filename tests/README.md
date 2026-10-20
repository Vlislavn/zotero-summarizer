# Tests

Follow the isolated test/runtime rules in [architecture](../docs/architecture.md).
`test_calibration_load_admission.py` runs real calibration and factory admission
against inert lower adapters: local and cached refusal, both constructors,
HTTP job status, hosted calibration and ordinary local application controls.
No live inference, credentials, network, private library or hardware load occurs.
