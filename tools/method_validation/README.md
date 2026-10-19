# Offline support comparison (#37)

`python -m tools.method_validation support --config FILE` validates frozen public
inputs without HTTP or writes. Add `--run` to consent to private artifact writes
and any explicitly qualified, already-running native HTTP arms. Reopen with
`python -m tools.method_validation support --inspect ATTEMPT_DIRECTORY`;
inspection never calls a provider or writes. There is no default endpoint/model,
server launch, credential lookup, retry engine, baseline constructor or model load.

```
explicit frozen configuration + exact original sources/pairs
  -> whole-workload admission / development firewall / byte and work reservations
  -> configured native HTTP OR recorded terminal bytes + matched binary artifacts
  -> exclusive Settings.data_dir/support_comparison/<attempt>/
  -> per-pair terminal rows + report.json / report.md + manifest / ACK
```

The frozen public API and all input/output ownership live in
[INTERFACES.md](INTERFACES.md). Full configuration, baseline limitations, protocol
source binding, resource caveats and study status are in
[the usage contract](../../docs/decision-support-comparison.md). These are ordinary
API/usage documentation, not executable prompts, configuration assets or datasets.

The CLI deliberately bypasses app bootstrap. Only write-consented execution imports
`Settings`, selecting the explicit project root with `.env` disabled via the null
device. All generated files stay in a new attempt under its `data_dir`. Existing
attempts, including incomplete/failed attempts, are never reused. A writer exception
marks FAILED and propagates, even if the file it wrote is readable. ACK is written
only after required publications return; reopening rejects FAILED, missing ACK,
foreign/partial inventory, code/schema/input hash drift or inconsistent projections.
Hashes establish integrity relative to the manifest, not signed authenticity.

Regression sources: `tests/test_support_comparison.py`,
`tests/test_native_decision_cli.py`, `tests/test_support_metrics.py`, with public
on-disk fixtures in `tests/test_support_fixtures.py`. They exercise the real parser,
HTTPX MockTransport, evaluator, artifacts and replay. They are wiring oracles, not
scientific quality or real endpoint qualification. Parent-controlled execution is
required; source delivery alone is not a runtime PASS. Frozen file references use
`pathlib.Path` with explicit absolute-path admission, supported by the installed
Pydantic. Guarded parent checks include a real isolated module-entry subprocess
for admission, recorded execution, inspection and refusal to reuse an existing owner.
Native readouts also pass the existing typed contract inside the provider projection
boundary: malformed Unicode aliases remain per-pair invalid responses, preserving
raw bytes and allowing valid neighboring cases to finish.
