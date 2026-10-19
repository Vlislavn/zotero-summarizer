# Regression tests

Audit tests exercise original-source verification, correction, cache retention and
read-only export using scripted clients and temporary files. They establish machine
contracts, not model accuracy, paper completeness or human-audit benefit.

`test_digest_contract_payload.py` checks that unrelated runtime subtype metadata
cannot change the declared digest attempt identity; plain digests and original
model values remain unchanged. No extra model or native-library call is needed.

Use the repository verification commands and the local source-bound evidence;
scoped checks do not substitute for full hooks, backend or hosted CI.
