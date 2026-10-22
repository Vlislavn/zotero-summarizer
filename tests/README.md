# Tests

Run focused cases with `pytest -q tests/<test_file>.py`. Tests use the existing
pytest fixtures and isolated application state; do not point fixtures at a live
Zotero library, provider, account or model. Feature-specific regression contracts
are documented with their owning service package and usage instructions.

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
