# RepoDoc: primary-artifact reconciliation (#23; #7 boundary)

## Decision and scope

**Reject the inspected RepoDoc call graph as an automatic architecture oracle.**
Retained graphs contain source-demonstrable false calls; historical inspection
ascribed them to the pinned upstream resolver. Do not add a dependency, CI job,
dashboard or parallel architecture framework. The historical Python-backend
checkpoint reports a failed caller-impact oracle. This is not a verdict on unobserved whole-system cost,
model quality, CLI completion or human comprehension: those remain **UNKNOWN**.
The parent-run two-file parser arm also fails all three correctness discriminators.
**These are completed diagnostics, not product delivery or correction GREEN.**
The complete current-repository experiment and issue closure remain incomplete.

The two complete conflicting seed reports are preserved in [HISTORY.md](HISTORY.md).
This report supersedes their unsupported numbers and success/failure narratives.
[PRIMARY_RECONCILIATION.json](PRIMARY_RECONCILIATION.json) separates current
availability from historical checkpoint records. Raw graphs stay in private,
gitignored `data/issue_runs/23/`; no embedded source is republished.

### Publication qualification: 2026-10-18

Local stdlib checks re-opened all six historical graph files: hashes, bytes and
declared counts match; dependency/KG IDs and call pairs agree. Tree membership is
113 exact IDs, 94 unique names and two ambiguous names. These checks import no
application or upstream code and do not establish graph correctness.

The six named backend execution artifacts, seven parser artifacts and upstream
source packet are unavailable at their declared locations. The execution/source
verification statements below are preserved historical checkpoint accounts, **not
current raw-evidence verification**. Missing files do not prove nonexecution or
loss everywhere. Historical verifier booleans retain their original scope; JSON
uses `historicalBackendExecution`, `historicalParserExecution` and
`historicalSourceCrossChecks`, without old-name aliases. No diagnostic replay,
private-source transmission, installation, model or issue closure is authorized.

Input is source1065 candidate `f66b78dde325a0fc0db1a0fff13302f7efd5e891`,
seeded into the exclusive lane at HEAD `bd5e26741dd70f3a359b984f759d68b32ac1eab9`.
The retained graphs are older: all 423 represented file paths still exist, but
807 of 2,585 embedded component snippets are no longer exact substrings of current
files (1,778 are). This limited substring comparison is not full-file identity,
code equivalence or evidence that the old graph describes current source.

## Primary facts versus historical narratives

Counts below were recomputed from the retained JSON with Python stdlib only;
no application/RepoDoc import, provider request or test process was used.

| Primary artifact | Verified content | Reconciliation |
|---|---|---|
| Source-only dependency JSON | 2,585 nodes; 4,539 `depends_on` entries; 1,799 backend, 413 frontend, **373 tools** | README's counts match this file; REPORT's 2,458/4,620 do not. It was not just backend + frontend. |
| Knowledge graph JSON | 2,585 code nodes, 4,539 calls edges; same IDs and edge pairs as dependency JSON; no missing endpoint | A KG exists. Its presence does not prove generation/model success or freshness. |
| Module-tree JSON | 41 roots; zero children; 209 membership strings: 113 exact IDs, 94 uniquely name-resolvable, two ambiguous | It is neither the claimed two-batch tree nor an 8-15-domain hierarchy. Unique-name reconstruction is diagnostic only, not an accepted identity repair. |
| Backend-only JSON | 1,799 nodes / 2,833 dependency entries | Separate run/scope; do not combine it into invented current metrics. |
| Frontend-only JSON | 413 nodes / 424 dependency entries | Historical, not a current frontend analysis. |
| Triage-only JSON | 188 nodes / 235 dependency entries | REPORT's 192/244 are not bound to this retained file. |

The 207 exact/uniquely name-resolved tree members are all classes. The dependency
JSON has 255 classes and 2,330 functions. Pinned upstream
`repodoc/src/analysis/dependency_graphs_builder.py:24-75` filters clustering leaves
to class/interface/struct whenever those types exist. This explains why treating
this tree as a whole functional-domain clustering, including the function-based
frontend, is inappropriate; it is not proof that a model dropped every function.

Durations (1.7 versus 2.95 seconds, 137 versus 187.91 seconds), RSS, selected-file
counts, provider/model/401/400 outcomes, token counts (zero versus >=10,001),
366 affected nodes/13 regenerations/five-minute timeout, and previous Vulture/
import-policy run results have **no inspected primary execution receipt** at the
named retained locations. Preserve these historical claims; do not pick whichever
narrative supports a decision. Missing receipts are UNKNOWN, not zero, success,
resource exhaustion or failure. No historical timestamp or revision is promoted
to source identity merely because it appears in a filename/report.

## Policy-grounded diagnostics, not semantic smells by label

1. **False storage-to-service call, independently source-checkable.** The retained
   graph links `storage._repo_feedback.get_feedback_events` to
   `services.faithbench._runner.execute`. Embedded source instead invokes
   `conn.execute` on the SQLite connection; current `_repo_feedback.py` preserves
   that operation. This is a call-resolution defect, not a product dependency.
2. **False global hub.** `frontend.src.utils.relevanceBands.get` has 600 graph
   callers and a self-edge. Its source is a local arrow helper inside
   `hydrateFilters`, calling `searchParams.get`. Current source preserves this
   lexical scope. It cannot be a global Python/backend dependency.
3. **Causal source mechanism.** At unchanged pinned upstream HEAD,
   `repodoc/src/analysis/call_graph_analyzer.py:207-225` puts bare function names
   into one lookup and resolves attribute suffixes through that lookup without
   receiver, lexical or language scope. The JavaScript analyzer also extracts
   nested arrow functions using module-only IDs. Both mechanisms fit the actual
   retained counterexamples; no application patch can make this graph sound.
4. **Do not conflate calls with import policy.** The repository's
   `tools/precommit/check_import_policy.py` analyzes static imports (including
   aliases/relative/nested forms) and permits `services -> api.errors`. A raw
   `services -> api` call aggregate is not automatically a violation. The current
   policy gate was read but not run; an old zero-violation narrative is not proof.
5. **Isolation and hub ranking remain candidates.** There are 211 historical
   graph isolates. Largest fan-outs are `LibraryReadNext` 26,
   `train_and_save` 25 and `run_daemon_tick` 25; these are raw graph facts, not
   policy thresholds or usefulness measurements. No node is deleted or called
   dead. Current Vulture/consumer cross-checks, cycles and placement disagreement
   remain unverified. The old 63-versus-two cycle narratives cannot be reconciled
   by declaring either the current truth.

These are useful **tool-correctness questions**, not the issue's requested useful
non-obvious current **application architecture smell**. That acceptance gate
remains open.

## Views and readability

The retained `data/issue_runs/23/report-rendered-1.png` (784x484) and
`report-rendered-2.png` (784x525) were read in this work session. The first has
9 path-aggregated nodes: API, services, storage, integrations, core models,
frontend, MCP, CLI and tools. The second shows selection, daily slate, daily actions, daemon, summarization,
backlog/jobs, library, model, storage and integrations.

Those historical images visibly project the false cross-language/backward
edges. They are not current source1065 KG/clustering views, not render-gate
receipts, and not proof of about-30-second comprehension by a source-naive human.
No new generated PNG, diagram, UI or agent surface is introduced here. The
curated architecture document remains unchanged; no verified correction to
its product loop or layering was found. A future current 8-15-domain system view
must separate measured call data from documented policy, and must be rendered,
read and pass the approved visual/UX gates before acceptance.

## Historical backend checkpoint: reported failed caller impact

The following preserves the checkpoint account for
`data/issue-validation/wave43/architecture43/kg-impact-01/receipt.json`, which then
superseded queued/unrun status. Its raw files are now unavailable for reverification.
That earlier reconciliation described stdlib inspection, not a new diagnostic run.
All **366** before-snapshot inputs match the source1065 seed; all **22** embedded
`loaded_upstream` identities match pinned source. The requested standalone
`loaded-source.json` does not exist; provenance is embedded in `receipt.json`.

| Observed result | Scope / interpretation |
|---|---|
| Before and after dependency/KG: **2,354 nodes / 4,120 edges** | Python backend only. IDs and call-edge pairs are equal in both snapshots; not comparable to the older combined graph's scope. |
| Only scratch `select.py` changes; only `plateau_select`'s source/parsed positional parameters change | The optional `_architecture_probe` parameter is observed. The two complete builds are a counterfactual, not incremental refresh or artifact preservation. |
| Backend changed/affected set: `plateau_select`, `SelectionResult`; known caller absent | File-level matching includes the unchanged class in the same file; this is not semantic change detection. |
| Direction control expected caller + callee; actual **callee only** | `matches_caller_impact=false`, `known_caller_in_impact=false`. The three-node/one-edge control contains a verified real call, not a package-import or model failure. |
| `unrelated_untouched=true` | Only the isolated helper in this control, not unrelated application modules/views. |
| Inner duration **2.4847991669084877 s**; receipt LLM calls/tokens **0/0** | Covers imports, two parses/graph saves, impact and loaded-source collection. Excludes snapshot/setup and receipt publication; no full-system benchmark or universal provider instrumentation. |
| Separate guard: max sampled tree RSS **97,517,568 bytes (93 MiB)**; max sampled swap growth **0** | Narrow backend arm only, 1-second polling; not continuous peaks or whole-system costs. |
| Guard exit **0**, outcome `completed`; released with no group members | Diagnostic completion, **not** a passing caller-impact oracle or acceptance. Parent admission 24.635% available; worker renewal 24.537%. |

**Mechanism established for this control:** the parser adds each callee to its
caller's `depends_on`; `graph/builder.py:12-37` creates **caller -> callee** edges.
The graph's `successors` are outgoing neighbors. The updater's
`_get_downstream_components` (`incremental/incremental_updater.py:136-162`) walks
those successors even though its contract says components that depend on the
changed component. With `run_daily_selection -> plateau_select`, the changed
callee has no successor, so the retained result is exactly callee-only. File
matching succeeded and no module-tree filter was supplied: neither explains the
missing caller. `get_upstream_dependencies` conversely walks predecessors despite
claiming dependencies. No upstream or application patch, edge reversal or oracle
relaxation was performed. CLI behavior is a separate unexecuted requirement.

## Historical parser checkpoint: three reported discriminators FAIL

The historical account reports parent execution of `parser-semantics-03` and
inspection of its receipt, frozen inputs, stdout, guard and release. Recorded
identities remain in [PRIMARY_RECONCILIATION.json](PRIMARY_RECONCILIATION.json);
those raw files are unavailable for this publication qualification.
Inputs are the existing seed-bound `_daily.py` and `_execution.py`, not a current
whole-repository scan or the parent's later integration candidate.

| Correct-behavior oracle | Actual primary evidence |
|---|---|
| Detect an added keyword-only parameter | **false**, expected true. Source names change from `feed_library_ids` to `feed_library_ids, _architecture_probe`; parsed parameters stay `[]`. |
| Retain post-nested outer raw call | **false**, expected true. `run_blocking -> asyncio.wait_for` at `_execution.py:33` is missing; pre-nested `copy_context` is present, nested `call` ID is flat. |
| Reject unrelated bare-name absorption of receiver methods | Absorption **true**, expected false. All five real `feeds_cfg.get`/`selection_cfg.get` calls are unresolved in the two-file baseline, then resolve to `diagnostic_receiver_control.get`. |

The witness is explicitly synthetic, in-memory and **not an application edge**.
Raw call loss is measured before resolution, so missing external nodes cannot
explain that discriminator. This confirms the prepared mechanisms, not a new
application layering defect. Existing backend SQLite `conn.execute` still falsely
resolves to `faithbench._runner.execute`; it is a separate actual application-source
counterexample, not an edge produced by this two-file witness.

Receipt: **19** already-loaded upstream references, **0.164493374992162 s**,
LLM calls/tokens **0/0**. Guard exit 0/`completed`; release records all67 upstream
files/two lane inputs unchanged, released with group `[]`, wholeAcceptance=false
and licenseAdoptionGranted=false. One-second polling captured **229,376 bytes**,
then 0 after exit: the short run fits between samples. **Not actual peak RSS or a
meaningful process-footprint estimate.** Sampled swap growth 0 is not zero total
system swap. Inner duration includes imports/analysis/provenance/rechecks, excludes
receipt publication and outer setup; no whole-system benchmark follows.

[NEXT_ARM.md](NEXT_ARM.md) now records the completed arm; it schedules nothing.
The original preparation and frozen records still say NOT RUN/runtimeGranted=false
because they describe preparation, not the later parent grant. Preserve them,
the missing-sysctl refusal and earlier unrun parser02 packet unchanged. Execution
supersedes only parser03's active status, not unmeasured historical report claims.

## Causal design-transfer / defer map

Anchors below describe the historical inspection of RepoDoc v0.1.0 at the pinned
HEAD. The raw upstream packet is currently unavailable for reverification. This
is a preserved design assessment, **not permission to patch, copy or adopt code**.

| Source mechanism and demonstrated limit | Transfer / defer | Minimum future corrective requirement |
|---|---|---|
| `analysis/analyzers/python.py:_process_function_node` reads only `node.args.args`; actual keyword-only probe misses the change | Adapt the existing source-to-component seam only after legal clearance; reject `[]` as a complete signature | Preserve argument kind/order, positional-only, keyword-only, variadics, defaults and annotations; distinguish unknown from empty. Discriminating controls must cover each kind, unchanged signatures and same-source reload, not just this name. |
| Same visitor overwrites/clears `current_function_name`; `_get_component_id` omits enclosing functions; raw post-nested call fails | Adapt lexical context handling at the visitor, not the application | Save/restore enclosing function/class context and use scope-qualified IDs; retain pre/post-nested calls and nested ownership through multiple depths, sync/async functions, methods and repeated local names. |
| `call_graph_analyzer.py:207-225` globally resolves bare names/attribute suffixes; receiver witness absorbs five real calls | Reject suffix-only resolution as authority; reuse raw qualified calls rather than inventing edges | Resolve only with justified lexical/import/receiver/language identity, otherwise keep unresolved/ambiguous. Also prevent `ast_parser.py:_build_components_from_analysis`'s bare-name fallback from bypassing that contract. Keep positive local/import controls and unrelated `.get`/`.execute` collisions. |
| `ast_parser.py:_build_components_from_analysis` and `graph/builder.py:12-37` store caller -> callee; `incremental_updater.py:136-162` follows successors as affected dependents; actual caller control fails | Adapt direction-aware traversal only after graph identity is sound; do not reverse every edge | For calls, affected callers follow predecessors and direct dependencies follow successors. Define semantic-impact direction separately; preserve cycle termination, unchanged/unrelated components and stable module IDs. Full CLI structural refresh/artifact preservation still needs its own gate. |
| `dependency_graphs_builder.py:24-75` uses class/interface/struct leaves; inspected `cli/commands.py:369-833` regenerates agent docs, not a refreshed/reclustered KG | Defer hierarchy/CLI reuse and any thin visual projection; reject the present graph as an automatic architecture oracle | After legal and correctness gates, include function-based domains and exact component identities; demonstrate current system/triage hierarchy and actual structural update without unrelated-artifact changes. Source inspection is not a CLI/clustering run. |

The reusable *shape* is source components -> dependency/KG -> domain projection ->
impact-scoped refresh, with provenance retained at the existing seams. Its current
implementation is not sound enough for automated diagnosis. Fixing traversal alone
would propagate omitted/false edges. No second graph, parser, dashboard, framework,
runner or case-specific application workaround is warranted. Future controls are
requirements, not tests added here, a prepared arm, execution approval or licensing
clearance. First establish legal transfer permission; any correction and tests need
a separately owned source slice and parent runtime admission.

The inspected CLI `update` (`repodoc/cli/commands.py:369-833`) loads a KG/module
tree and regenerates documentation through an agent; it does not save a newly
parsed dependency/KG or recluster views. No inspected no-model graph/view-refresh
flag exists there. `compute_impact_graph` filters root module-tree strings by exact
IDs; name-only members are not transparently repaired. These are source facts,
not CLI execution receipts. Resource statistics are absent from the inner receipt,
but the separately inspected `kg-impact-01-parent-guard02` guard/release receipts
establish the narrow sampled RSS/swap values above. The earlier prelaunch refusal
records missing `sysctl`, empty guard JSONL and `productLaunched=false`: transport/
guard setup failure, not a RepoDoc/model failure. Historical timings/model outcomes,
hierarchical clustering, full-system costs and native CLI refresh remain UNKNOWN.

The assigned interpreter lacks `tree_sitter`, `tree_sitter_javascript` and
`tree_sitter_typescript` package directories. The inspected JavaScript analyzer
requires them; its per-file import failures are caught by the analyzer. A whole
frontend scan now would misleadingly return partial results. No install,
substitute parser/graph, fake semantic clustering or provider request is proposed.
The completed backend and parser arms are causal slices, not product delivery or
silently narrowed #23 success.

Upstream version is 0.1.0, HEAD
`306becd0f143211c0dde2bdbd480578356280e28`, bound to the retained private source
packet. Repository metadata license is null and there is no license file:
**unknown is not transfer permission**. Source inspection/private diagnosis only;
no dependency, upstream code adoption, redistribution or license decision.

## Requirement-by-requirement remaining gates

| #23 requirement | Actual status / remaining gate |
|---|---|
| Current initial KG + hierarchical clustering | Historical checkpoint reports backend KG2,354/4,120; its raw files are now unavailable. Whole graph/clustering and frontend dependencies remain unresolved. |
| Meaningful domain/boundary clustering | Historical class-only, 41-root tree does not establish it; current API/services/storage/integrations/core/frontend/MCP mapping needed. |
| System 8-15 domains + triage drill-down | Existing old PNGs read; no current generated views/render gates. |
| Cycles/hubs/isolation/backward edges/placement | Two source-grounded graph defects verified; raw 211 isolates/hubs retained; no current code-policy/Vulture/placement result. |
| Controlled structural change / update without unrelated rebuild | Scratch positional-signature change observed; actual caller-impact control FAILS. Full CLI diff/update and artifact preservation remain unverified. |
| Useful non-obvious application smell | Not established; demonstrated defects concern RepoDoc itself. |
| Deterministic rendering + low density | Old render exists, origin/gates unknown; current render/visual gates remain. |
| ~30-second source-naive comprehension | Human/device acceptance not measured. |
| Runtime/token receipts | Backend: 2.4847991669084877 s, sampled RSS 97,517,568 bytes; parser: 0.164493374992162 s, RSS sample misses the run's peak. Each records zero LLM calls/tokens and sampled swap growth 0. Full-system/historical costs UNKNOWN. |
| Adopt/adapt/reject | Reject inspected unsound graph as authority; integration withheld; unknown gates not claimed as failures. Reviewers supply findings; parent alone accepts or closes. |

## #7: preserve existing patterns, do not imply issue completion

`docs/adr-0001-llm-for-zotero-patterns.md` already makes the eight pattern decisions.
This slice does not alter it or acquire/copy AGPL code. Current source inspection
confirms `qa.py -> qa_context.compact_history` and current-extraction citation
checks; `services/llm/factory.py` forwards `provider.structured_output` into the
shared client. These are existing contracts, not new runtime validation.
Capability-controlled schema output is not proof that stage **selection** is
based on required capabilities. The ADR's licensing/source snapshot and accepted
status are historical assertions, not newly verified upstream-release evidence.

| #7 pattern / contract | Preserved evidence and remaining gate |
|---|---|
| Legal source + adopt/adapt/defer/reject ADR | Existing eight decisions retained; upstream source/release/license verification remains open. No copied AGPL implementation. |
| Compaction + verified citations | `qa.ask_paper -> qa_context.compact_history`, `verified_quote` and `citation` exist. Require bound multi-turn deterministic/real-paper evidence, retained exact handles, message boundaries, stale/reparse rejection and current-extraction location checks. |
| Required-capability routing | Factory forwards structured output for an already resolved stage; require at least one production workflow chosen by required capabilities with compatibility evidence, not provider-name dispatch alone. |
| Every-write safety | Existing approved-write work preserved, not surveyed/accepted here. Require typed preview/confirmation/audit across tags, notes, collections and materialization; inverse/backup/reversible-action coverage where possible, no free-form model writes. |
| Workflow policy + reusable coverage | ADR rejects a second skill layer and uses existing typed services/client-owned context. Require reviewed equivalence or explicit deferral of versioned trigger/evidence/tools/schema/abstention/evals and extraction-versioned coverage/reuse/reread accounting. |
| Setup + native/plugin/extraction scope | Existing diagnostics/deferrals preserved, not newly validated; required-capability probes and degraded-mode evidence remain separate. No plugin port or MinerU adoption without measured friction/corpus gain. |
| Evaluation + stop gate | Deterministic and real-paper sets, matched-baseline latency/tokens/citation precision/abstention/completion, two consecutive release reviews and no remaining upstream-required issue references remain open. |

The existing ADR, QA implementation and approved-write work are preserved;
production changes are outside this lane's edit paths. Parent owns architecture
synthesis and any production slices rather than treating this report as closure.

## Verification and simplification

**RED preservation evidence:** seed README and REPORT disagree on node/edge counts,
model success and update outcome; primary stdlib counts disprove REPORT's bound
numbers and expose unbound runtime claims. Original reports are retained verbatim.
**Reported historical runtime REDs:** caller impact returns only the callee;
three parser/resolver discriminators fail. Their raw evidence is now unavailable
for reverification; exit0 alone was never a passing oracle.
**Documentation reconciliation only:** active docs/ledger now consume both parent-run
receipts, retain exact expected/actual values and source mechanisms, separate
historical/backend/parser/CLI evidence, and keep missing gates UNKNOWN/open. No
runtime was performed at this checkpoint; no correction/test GREEN or fresh
independent review is claimed. No regression oracle, dependency or index changed.

Subtraction was sufficient: use one evidence ledger and existing source seams,
replace the queued parser narrative with its actual result, remove launch directions
from active NEXT_ARM and add no production code, tests, graph, store, queue, hasher,
grader, dashboard or runner. Further simplification should not remove the source
anchors or distinctions between synthetic controls and application edges. Fresh
readonly review remains required. Reviewers supply findings; parent alone owns
runtime admission, acceptance, integration, publication and closure.
