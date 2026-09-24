# RepoDoc architecture-diagnostics experiment

**Decision: reject RepoDoc 0.1.0 at upstream commit
`306becd0f143211c0dde2bdbd480578356280e28`.** It can inventory a source-only
snapshot, but its graph, clustering, and incremental impact are too noisy to be
an architecture oracle. Keep it out of dependencies and CI. Re-evaluate when it
honors ignore files, resolves symbols by scope, fails closed on LLM errors, and
bounds incremental fan-out.

The 2026-08-27 experiment used the dirty worktree. Runtime artifacts remain in
gitignored `data/issue_runs/23/`; no generated docs or third-party code are
committed.

## CAPA and evidence

| Finding | Evidence | Disposition |
|---|---|---|
| Full scan exhausted resources | Stopped at 187.91 s and 1.49 GB RSS. RepoDoc selected 6,465 frontend files plus 2,238 agent-config files because it ignores neither `.gitignore` nor `node_modules`. | Keep outside the repo; require ignore-file support. |
| Source-only parsing works | Completed in 2.95 s / 94 MB with 2,585 components, 4,539 edges, and 246 leaves. | Useful only as an experiment input. |
| Call graph is unsound | A nested frontend `get` helper received 600 callers; it also invented Python/frontend and reverse-layer edges while the import-policy check passed. | Require scoped symbol resolution and zero false layer edges. |
| LLM errors report false success | Deliberate 401s produced arbitrary `batch_N` groups while the CLI printed `Clustering done`. | Require a nonzero exit and no fallback graph. |
| Successful clustering is unreadable | GPT-OSS-120B produced 41 flat, duplicated groups with no children. The run used at least 10,001 observable tokens. | Require 8–15 coherent groups and a hierarchy. |
| Incremental impact explodes | One class-docstring edit expanded to 366 affected components and 13 regenerations, then stalled for five minutes without a receipt. | Require bounded impact matching the changed component. |

Owner for containment/corrective action: repository maintainer. Upstream owns
parser and clustering defects; a future evaluator owns the preventive rerun.

## Go / no-go gates

| Gate | Result |
|---|---|
| Understandable in about 30 seconds | **Fail** — 41 flat groups. |
| Low enough density to expose smells | **Fail** — 4,539 noisy edges. |
| Correct against code/import policy | **Fail** — false cross-language and reverse-layer edges. |
| Answers a useful non-obvious question | **Fail** — reported hubs/cycles do not survive independent checks. |
| Incremental update follows impact | **Fail** — one edit expanded to 366 components and stalled. |
| Deterministic GitHub rendering | **Partial** — Mermaid renders, but useful grouping needs path-specific glue. |

The apparent 211 isolates, two multi-node cycles, and high-fan-in hubs all fail
cross-checks against Vulture and the executable import-policy gate. The existing
curated Mermaid views plus import, LOC, overlap, and dead-code checks are smaller,
deterministic, and more trustworthy.
