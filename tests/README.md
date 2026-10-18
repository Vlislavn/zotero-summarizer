# Tests

```text
Search refinement -> exact DOI/arXiv coverage -> version family -> saved session
                                                        -> explicit Add reuses key
```

`test_search_refinement_coverage.py` exercises the existing reader's tri-state
contract, both family orders, persisted membership/review/address retention, and
explicit Add without constructing a native writer for an already-saved result.
It uses fake retrieval/readers and isolated session storage, not live accounts or
models. Run with `pytest -q tests/test_search_refinement_coverage.py`; repository
verification and the known macOS native-fork baseline are documented in
`../docs/architecture.md`. Authored tests are not a claim of execution.
