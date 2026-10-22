# Offline support and reading-priority comparisons

The additive `priority` command compares frozen title/abstract/goals against four
canonical reading priorities, preserving native choices/vectors and imported
regressor/LLM baseline capabilities. It shares the existing raw-terminal/transport
and artifact primitives, not a second client or model loader. Default admission
and artifact inspection perform zero calls/writes; only explicit `--run` writes a
new selected-root attempt. See [priority usage](../../docs/decision-priority-comparison.md)
and [its frozen API](PRIORITY-INTERFACES.md). Missing/invalid/blocked/abstained
outcomes stay unscored; synthetic controls and unknown resources cannot establish
a winner, promotion or full #38 completion. Reports keep per-gold-class failure
coverage separate from decision-only quality and identify synthetic labels plus
declared/missing provenance counts without claiming independent verification.
Admission rejects repeated canonical papers even within one family, and ML imports
require nonempty training-family provenance before any artifacts or requests.
Priority uses the same public native admission/TLS guard, retaining its four-way
projector and outcome-work reservation. Its separate aggregate `max_snapshot_bytes`
cap also bounds each input read before allocation. Priority rows without declared
human provenance never enter paired estimates; blocked recordings stay unscored.

## Support comparison (#37)

`python -m tools.method_validation support --config FILE` validates frozen public
inputs without HTTP or writes. Add `--run` to consent to private artifact writes
and any explicitly qualified, already-running native HTTP arms. Reopen with
`python -m tools.method_validation support --inspect ATTEMPT_DIRECTORY`;
inspection never calls a provider or writes. There is no default endpoint/model,
server launch, credential lookup, retry engine, baseline constructor or model load.

```
explicit frozen configuration + exact original sources/pairs
  -> whole-workload admission / development firewall / shared input-byte and work reservations
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
Recorded reopening additionally compares each copied terminal with the admitted
recording for that exact case/arm and requires the recorded received state. A
self-consistent replacement of terminal/derived report/hashes cannot substitute
another decision while leaving that bound input unchanged. Terminal references
must match their per-row retained files. Terminal-free outcomes reconstruct the
admitted blockage/recording presence and finite remaining-budget observation;
`blocked` cannot hide an invalid response or shrink evaluated coverage. Actual
wall exhaustion stays unscored, not blocked. Timing observations remain unsigned.

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

Direct native calls share the runner's consent/guard and next-call reservation check;
adapter instances also bound cumulative work. Recorded evaluation rejects blocked or
wrong-kind arms. Remote endpoints require HTTPS (loopback HTTP is retained). All frozen
input snapshots share `max_input_bytes`; paired inference excludes gold without human
provenance while retaining descriptive labels and the full coverage denominator.
