# Tests

Run focused cases with `pytest -q tests/<test_file>.py`. Tests use the existing
pytest fixtures and isolated application state; do not point fixtures at a live
Zotero library, provider, account or model. Feature-specific regression contracts
are documented with their owning service package and usage instructions.

`test_search_materialize_recovery.py` and `test_search_planned_identity.py` exercise
explicit Search Add with real temporary native SQLite: persistence refusal before
effects, native commit before failed session finalization, rollback, unchanged
native replay, backup, concurrency, legacy plans and exact-family/background
identity preservation. They do not claim distributed or cross-library exactly-once.

Full verification commands and the known macOS native-library fork baseline are
in [architecture.md](../docs/architecture.md#verify-a-change). Compare the failed
set against that baseline rather than hiding failures. Authored/static-checked
cases are not evidence of execution; report RED/GREEN and runtime limits honestly.
