# Completed diagnostics; no next arm authorized

## Backend `kg-impact-01`: retain the failed oracle

The parent-run source1065 Python-backend arm used 366 seed-bound files and the
pinned parser/graph/updater. Before/after: **2,354 nodes / 4,120 edges**, identical
IDs/call-edge pairs. Only scratch `triage/select.py:plateau_select` source and
positional parameters changed. Two full builds are a counterfactual, **not** an
incremental refresh.

The control expected `run_daily_selection` plus `plateau_select`; actual impact
contained **only `plateau_select`**. The isolated helper stayed untouched. The
backend affected set included the unchanged same-file `SelectionResult`, not the
known caller. Preserve `matches_caller_impact=false`.

Receipt: 22 loaded-source references, 2.4847991669084877 s, LLM calls/tokens 0/0.
Separate guard02: max sampled tree RSS 97,517,568 bytes (93 MiB), swap growth 0;
completed/released, not an oracle pass. Not whole-system costs or CLI preservation.

## Parser `parser-semantics-03`: executed, three discriminators FAIL

The parent's actual receipt supersedes **only this arm's** prepared-not-run status.
Two lane source inputs, pinned RepoDoc v0.1.0 HEAD
`306becd0f143211c0dde2bdbd480578356280e28`; no application module executed.

| Correct-behavior oracle | Actual |
|---|---|
| Adding keyword-only `_architecture_probe` changes parsed signature | **false**, expected true; source keyword-only names change, parsed parameters stay `[]` |
| Retain outer `run_blocking -> asyncio.wait_for` raw call at line 33 after nested `call` | **false**, expected true; pre-nested `copy_context` retained; nested ID is flat |
| Unrelated bare `get` must not absorb real receiver methods | Absorption **true**, expected false; five real calls resolve to the synthetic witness |

The receiver witness is an in-memory name-table control, **not an application
edge or third source file**. Raw post-nested call loss precedes resolution, so
unresolved external calls do not explain that discriminator.

Receipt: **19** already-loaded upstream references, **0.164493374992162 s**,
LLM calls/tokens **0/0**. Guard exit 0/`completed`; parent release records all67
upstream files/two inputs unchanged, empty group, released, wholeAcceptance=false,
licenseAdoptionGranted=false. One-second sampling records 229,376 bytes and then
0 after exit: **not actual peak RSS or a meaningful process-footprint estimate**.
Sampled swap growth 0 is not zero total system swap.

[PRIMARY_RECONCILIATION.json](PRIMARY_RECONCILIATION.json) binds receipt, frozen
stdin/authority/Markdown, stdout, guard and release. [REPORT.md](REPORT.md) maps
source mechanisms, concept-level transfer/defer and minimum future corrections.

## Preserve preparation; stop rather than replay

Original `prepared-parser-03` and the guard's frozen authority/Markdown retain
historical NOT RUN/runtimeGranted=false preparation fields. They are not current
status receipts and must not be rewritten to manufacture a grant. The original
`prepared-arm-01`, missing-sysctl refusal and earlier parser02 unrun packet remain
unchanged. Execution of parser03 does not recover their outcomes or unmeasured
historical README/REPORT claims.

**No new arm, runner, replay, upstream/application patch or runtime lease is
prepared or granted here.** This checkpoint reads source/evidence and reconciles
docs only. The earlier narrow readonly-import exception belonged to that named
parent-run diagnostic; current upstream authority is read-source-only, not import,
copy/adopt/redistribute permission. License metadata remains null/no license file.

Both runs are diagnostic evidence, **not product delivery, correction GREEN,
acceptance or #7/#23 closure**. Full hierarchy/frontend, current system/triage
views, application smells, native CLI structural update/preservation, whole-system
costs and source-naive comprehension remain open. Reviewers supply findings;
parent alone owns runtime, integration, acceptance, publication and closure.
