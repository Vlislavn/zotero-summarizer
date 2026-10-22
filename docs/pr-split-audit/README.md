# Original-change review index

```text
498 original paths / 1,727 original hunks
  ├─ 290: original complete blob exists in a public head
  ├─ 191: published changed version; original snapshot retained
  └─  17: explicitly superseded implementation variants

production changes ──> bounded feature/source PRs
original oracles   ──> separate source-only review PRs
old alternatives  ──> explicit disposition + immutable original
```

## Scope and evidence

This finishes **distribution/disposition**, not feature development, issue acceptance or deployment. Source authority is [`34371710619b647a778ea89db4125d24874cf5ac`](https://github.com/Vlislavn/zotero-summarizer/tree/34371710619b647a778ea89db4125d24874cf5ac), compared with `bd5e26741dd70f3a359b984f759d68b32ac1eab9`: 498 changed paths and 19,585 original production additions+deletions.

- [PR index and dependency bases](PR-INDEX.md)
- [Every original file, hash and review owner](SOURCE-FILES.tsv)
- Every original hunk, range, hash and disposition: [services](SOURCE-HUNKS-SERVICES.tsv), [other original files](SOURCE-HUNKS-OTHER.tsv)
- [Explicit superseded variants and current provenance](SUPERSEDED.md)
- [Working-source overlays](WORKING-OVERLAYS.json)

There are no unassigned source paths/hunks. **That is an inventory assignment, not a functional score or proof of semantic equivalence.** A changed-version row deliberately distinguishes the current implementation from the original bytes; it does not pretend a rewrite is byte-exact. Original bytes stay publicly inspectable. Superseded variants are not silently deleted or restored as duplicate authorities.

The 17 variant dispositions include nine absent older modules/fixtures and eight older shared-file changes. Notably, private/live interpretation-schema hooks are not the current frozen experiment; old context-local provider observers are not the current explicit receipts. Non-OpenAlex empty-channel status remains unknown, not full old status parity.

## Review and merge boundaries

```text
#144 -> #181 -> #192         signal display -> original reading oracles
#179 -> #182 -> #197         offline controls -> original offline/infra oracles
#167 -> #183 -> #198         RSS partition -> original metadata oracles
#140 -> #184 -> #193         PDF presentation -> original PDF oracles
#145 -> #185 -> #188         CLI consent -> original Setup oracles
main -> #199 -> #152         support foundation 987 A+D -> corrections 65 A+D
prerequisites -> #200 -> #153 discovery foundation 994 A+D -> corrections 94 A+D
```

The other oracle branches are listed with their actual bases in the PR index. A base is **not** a claim that all cross-feature prerequisites have been merged. Review the oracle/API pairing before merging source-only tests.

All review PRs at this snapshot are OPEN/non-draft/unmerged and within **1,000 production additions+deletions**, counted without netting. Tests/Markdown docs are excluded; configurations/assets count. The review TSV/JSON metadata also count, so this index is itself split into three dependent bounded PRs: file/disposition ledger, service hunks, and other hunks. The two foundation cuts use existing ancestor commits; original #152/#153 heads were not reset, rebased, force-pushed or changed. Only their GitHub PR bases changed, with acknowledgments and explanatory comments.

## Verification, without inflated claims

| Packaged change | Observed verification |
|---|---|
| Original signal display (#181) | 13 existing frontend cases; Vite build and actual assets |
| Offline paper controls (#182) | 2 original DOM cases; Vite build and actual assets |
| RSS producer-owned partition (#183) | 7 original scripted production-phase cases; synthetic owned SQLite |
| PDF presentation caller (#184) | 42 retained render/brief cases |
| Default-no CLI inference consent (#185) | 9 consent/status cases; no endpoint/model execution |
| Existing support foundation (#199) | 74 retained synthetic cases |
| Existing discovery foundation (#200) | 93 retained synthetic cases |
| Original source oracles (#186–198) | Exact original bytes + all canonical source hooks; **not behavioral acceptance** |

All new candidates and both existing-ancestor cuts passed all nine canonical hooks on bound staged bytes and strict secret scanning. Product checks ran sequentially through the existing guarded runners. Counts have different scopes and are not an aggregate competence score.

The 113 previously unassigned original test/docs paths are preserved in 13 source-only PRs (#186–198); two more original suites accompanied #182/#183. Assertions, skips and xfails were not rewritten to fit new implementations. **Known mismatch:** the original `briefModel.test.js` produced five formatting-contract failures against the current formatter; it is retained for review in #192, not claimed compatible. Other older/private oracle/API combinations were not qualified by running a whole acceptance suite. Source-hook success must not be read as CI-green integration or merge readiness.

No real model, private library, live account, native-process/platform recovery or human/scientific acceptance is inferred. Review/merge and issue acceptance remain with the user. Earlier invented Feedback-export prototypes remain preserved locally and unpublished, outside the original-change split.

## Reproducibility and preservation

Private local receipts live under `data/functional-prs-final-bbeac9bd/packaging-final-v165/`, adjacent publication directories, and guarded `validation/` results. The ledger checks original Git blob identity, recounts every original hunk against the inventory, and uses actual GitHub changed-file owners. Its generated tables contain metadata, not relocated production code.

Original working checkout/index were preserved in place; no stash or destructive cleanup. Index SHA-256 remained `eb7d780fd03cb8aa30be60297e53f75ec509e81ce9d458ae2d4d009fd0e8461e`. Aggregate historical README/changelog prose is not copied over current package-specific documentation. The remaining working overlays have explicit receipts; the replay-test adjustment is AST-identical with one EOF blank removed.

Simplification assessment: one review index, no new product abstraction, compatibility facade, model route or UI control. Keep one canonical owner per current protocol; preserve competing historical source as evidence, not as a second runtime authority.
