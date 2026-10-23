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

Search family-identifier regressions cover normalized retained IDs, transitive
merges after JSON reload, positive-key affinity, partial-reader uncertainty, and
persisted refinement with older misses and newly unresolved identities.
Controlled identifiers/readers establish machine contracts, not live-library
completeness or semantic paper equivalence.

`test_discovery_server*.py` covers API/SQL/privacy/race/effect contracts, including
all-response sentinels and alternate-action fences, using synthetic stores and
a controlled provider bridge. Scripted judgments do not prove usefulness.

`test_discovery_provider_*.py` covers inert OpenAlex JSON receipt contracts,
concurrent variant isolation and unchanged narrow ordinary callers for other sources.
Wider arXiv/Europe PMC receipt scope is deliberately excluded from this version.
`test_discovery_recovery_joint.py` and `test_discovery_existing_only.py` cover real
temporary native SQL, strict current no-op ACKs, save gaps, malformed receipts,
WAL visibility and refusal before mutation/backup/pruning. Read-only ACKs remain
available with the connector running; ordinary writes still refuse. These
machine controls do not qualify live providers or semantic recommendation quality.
Discovery's disabled-reranker route regression uses the actual scorer and fails
on any model construction; disabled mode retains real lexical scores. An absent
native reader leaves external membership unknown, never recommended as new.
Held-provider and assessment-upgrade reads also hide private context and default
recommendations until Discovery completes; failed retrieval retains no private view.
Real RuntimeState client construction is covered with prestige off and with/without
an existing cache, avoiding fake dependency objects that hide ownership errors.
Disabled optional metadata clients still prepare the existing cache schema before
Discovery identity, preventing first-client self-invalidation.
Identifier-linked unmaterialized app notes (direct and transitive DOI/arXiv aliases)
are submitted only to the consented model; edits/clears stale and hide prior context.
OpenAlex location-alias controls use real leaf parsing/federation to exclude a
renamed/re-DOIed saved seed by DOI or versioned arXiv location while retaining a
same-title distinct-ID paper. Receipt-on/off and lookalike-host cases are covered.
Candidate identity regressions also distinguish public metadata changes from later
derived operation/question-evidence enrichment; source inheritance is not identity.
