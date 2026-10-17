# RepoDoc architecture-diagnostics experiment

**Reject the inspected graph as an automatic architecture oracle; keep integration
withheld.** Two false call edges are verified against embedded and current source,
and explained by the unchanged upstream global name resolver. The retained current
backend arm also **fails caller-impact propagation**. Neither fact turns missing
whole-system/runtime receipts into failures. **#23 remains incomplete.**

Start with [REPORT.md](REPORT.md). It reconciles the conflicting seed reports
against hash-bound primary JSON: 2,585 nodes/4,539 edges, a 41-root tree with no
children, and a separate 188-node/235-edge triage graph. These are historical,
not current source1065 results. A separate source1065 Python-backend receipt now
establishes 2,354 nodes/4,120 edges, a failed caller-impact control, a recorded
2.4847991669084877-second inner duration and zero LLM calls/tokens. Its separate
guard records max sampled tree RSS 97,517,568 bytes (93 MiB) and swap growth 0:
this narrow diagnostic completed, but its oracle failed. Full CLI update,
historical provider outcomes and whole-system costs remain UNKNOWN.

The parent also executed `parser-semantics-03` on two seed-bound files: all three
correctness discriminators fail (keyword-only signature, outer context after a
nested function, receiver resolution). Its 19 loaded-source references are bound;
inner duration is 0.164493374992162 s, LLM calls/tokens 0/0. The 229,376-byte RSS
sample is **not a peak or process-footprint measurement**. This is diagnostic
completion, not product delivery, correctness, acceptance or closure.

- [PRIMARY_RECONCILIATION.json](PRIMARY_RECONCILIATION.json): source/artifact
  identities, actual counts, code-policy cross-checks and unknowns.
- [HISTORY.md](HISTORY.md): both complete original reports, byte-preserved inside
  fenced blocks with their seed hashes; superseded, not execution proof.
- [NEXT_ARM.md](NEXT_ARM.md): both completed diagnostics and their failed oracles;
  the frozen preparation/refusal/unrun preimages stay unchanged. **No next arm or
  replay is authorized.** Source-anchored design-transfer/defer requirements are
  in REPORT; no frontend/CLI/comprehension claim.

Pinned RepoDoc is v0.1.0 at
`306becd0f143211c0dde2bdbd480578356280e28`. The private source packet has null
license metadata and no license file: no copying, adoption, redistribution,
installation, dependency or CI change. Existing graphs/PNGs stay in gitignored
`data/issue_runs/23/`; the curated `docs/architecture.md` is unchanged.

A future full experiment still needs current KG/hierarchical domain clustering,
8-15-domain system and triage views with render/visual gates, code-policy-grounded
smells with dead-code cross-checks, controlled incremental artifact preservation,
actual runtime/token receipts, and source-naive human comprehension. Existing #7
ADR/QA/provider/write work is preserved, not accepted as complete by this slice.
Reviewers provide findings only; **the parent alone accepts or closes**.
