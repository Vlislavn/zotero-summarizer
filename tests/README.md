# Regression tests

The application fixtures isolate runtime paths and network; see `conftest.py` and
`docs/architecture.md`. No test is permission for live models or private data.

## Recorded-input replay regression

`test_recorded_support_binding.py` replaces a copied terminal and recomputes all
derived rows/report/hashes/ACK while leaving the frozen recording unchanged.
`test_support_replay_states.py` additionally removes terminal references/bytes,
forges recorded/native blocked states and request identities, and preserves actual
blocked/missing/wall/malformed-neighbor states and coverage denominators.
Reopening must reject this input-to-output substitution, independently of unsigned
artifact authenticity. Existing recorded/native/error/reopen controls remain.

## Offline support comparison (#37)

Parent-controlled focused selector (existing pytest installation, no dependency sync):
`pytest -q tests/test_support_comparison.py tests/test_native_decision_cli.py tests/test_support_metrics.py tests/test_native_decision_import.py`

- `test_support_comparison.py`: exact Unicode/repeated-span admission, source hashes,
  gold identity joins, dev/holdout firewall, actual binary baseline/omission preservation,
  unavailable artifacts, exclusive owner, corruption, write-then-raise and reopen.
- `test_native_decision_cli.py`: real parser/handler/HTTPX MockTransport/evaluator/artifact
  assembly; gold sentinels absent from captured requests; model/schema/vector failures,
  bounded raw/partial terminal retention, blocked arms, resource/work caps and production
  write/model-construction spies. Escaped high/low surrogate aliases fail only their
  pair; valid Unicode mismatches and valid neighboring cases preserve their results.
  No recorded or mocked response is a native smoke test.
- `test_support_metrics.py`: hand-computed error/Brier/denominator/class fixtures and
  equal-family binary-loss bootstrap floors; perfect synthetic data cannot imply a winner.
- `test_native_decision_import.py`: isolated import against installed Pydantic and
  absolute frozen-input admission; catches unavailable type imports before app execution.
  A real isolated module-entry subprocess also admits without writes, executes recorded
  responses, inspects without mutation and refuses a second invocation's existing owner.
- `test_support_fixtures.py`: public synthetic on-disk fixture helpers, not production
  data or external human-labelled quality evidence.

Source delivery left these tests UNRUN. The first guarded parent execution hit three
collection errors: Pydantic exports no `AbsolutePath`. The standalone import regression
then failed normally before the correction. Runtime reruns remain separately recorded;
AST parsing alone never establishes runtime success.
