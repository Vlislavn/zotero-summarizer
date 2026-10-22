# Tests

Run focused cases with `pytest -q tests/<test_file>.py`. Tests use the existing
pytest fixtures and isolated application state; do not point fixtures at a live
Zotero library, provider, account or model. Feature-specific regression contracts
are documented with their owning service package and usage instructions.

`test_search_materialize_recovery.py` and `test_search_planned_identity.py` exercise
explicit Search Add with real temporary native SQLite: persistence refusal before
effects, native commit before failed session finalization, rollback, unchanged
native replay, backup, concurrency, legacy plans and exact-family/background
identity preservation. A later-family identity conflict leaves all input candidates
unchanged. Delivered coverage conflicts and replay responses are checked without
claiming a changed collection. These tests do not establish distributed or
cross-library exactly-once.

Full verification commands and the known macOS native-library fork baseline are
in [architecture.md](../docs/architecture.md#verify-a-change). Compare the failed
set against that baseline rather than hiding failures. Authored/static-checked
cases are not evidence of execution; report RED/GREEN and runtime limits honestly.

Search family-identifier regressions cover normalized retained IDs, transitive
merges after JSON reload, positive-key affinity, partial-reader uncertainty, and
persisted refinement with older misses and newly unresolved identities.
Native-reader tests use in-memory SQLite to check unsafe arXiv wildcard aliases,
safe DOI fallbacks and ordinary arXiv hits/misses. Controlled identifiers/readers
establish machine contracts, not live-library completeness or semantic paper
equivalence.
