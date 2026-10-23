# Research-feed evaluation evidence for issue #12

The offline acceptance command currently exits nonzero:

```bash
uv run python tools/eval_research_feed.py --check
```

The shipped 30-row fixture lacks complete frozen pre-human cohorts and review
outputs. Contract v2 reports unavailable (`null`) production inclusion, recall,
reading/card/artifact/time metrics and `passes: false` (exit 1). Historical v1
calculations live only under `legacy_v1_diagnostic`; the separate 17-row reading
agreement is recomputed against current policy, not fixed as an acceptance score.
A wiring projection is not a measured production result.

## Why the result cannot validate the product

`tools/research_feed_fixture.json` contains paper titles, human inclusion and
must-not-miss labels, decision labels, project labels, and verified repository
URLs for some rows. It does not contain abstracts, canonical source identifiers,
or the frozen pre-human triage evidence used in production (`composite_score`,
`reading_priority`, and the stored model summary).

Version-2 replay requires a complete frozen pre-human B1-output cohort, explicit
canonical case IDs, source inputs, accepted raw history and independent labels
joined by identity. It validates native/numeric evidence and reuses the production
whole-cohort ranker once before top ten; labels never feed the projection. Missing
cohorts remain unscorable. A declared complete but malformed snapshot fails loudly
rather than reducing the cohort or coercing missing evidence to zero. Zero numeric
observations are distinct from absent inputs; booleans are not numeric observations.

Conditional shortlist precision and eligible must-not-miss recall describe only
that complete qualified cohort. They do not establish full native-admission recall,
capture authenticity, review quality or project usefulness. Those metrics remain
unavailable and cannot make `passes` true. Legacy `frozen_machine` rows can still
exercise the historical fallback/ordinal calculation, but no longer license the
primary production-ranking metric. The six frozen legacy modes retain their math;
the independently approved reading-policy migration affects only its diagnostic.

The previous artifact URLs were copied from `verified_code_url` gold into a
synthetic review; that by-construction comparison is removed. Only independent
frozen review outputs and manually verified artifacts can license artifact
precision. Human time and generation tokens/cost are similarly unavailable,
not zero. Passing human decisions to triage would leak the answer.

Exact-title lookup can recover some independent public abstracts, for example
[SwarmWorld](https://arxiv.org/abs/2608.26081),
[GTA-RAG](https://arxiv.org/abs/2608.22479), and
[When Stale Constraints Go Unchecked](https://arxiv.org/abs/2608.25553). The
fixture does not identify all 30 rows with canonical URLs/IDs, and abstracts
alone would still not supply the frozen production triage outputs required by
this projection evaluator.

The separate 17-row `reading_policy_fixture_v2.json` supplies cached-policy
agreement under `reading_policy_fixture_agreement`, not frozen research-feed
model inputs. Unconfirmed digest basis cannot authorize automatic full reading;
these unchanged annotations do not provide source-qualified full-read precision.
It must not be used to claim that issue #12's paper-inclusion benchmark passes.

## Reproduction evidence is separate

Weekly cards retain `reproducibility_tier="unknown"` until reproduction effort
and required inputs are assessed. A working repository URL is not a one-week
estimate; missing scientific checklist criteria are not missing reproduction
materials. Artifact URLs remain visible and scientific gaps use `evidence_gaps`.
This projection change does not supply the missing cohort or human judgments.

## Weekly consumption and cursor boundaries

The Weekly page consumes retained bundles and explicitly builds from saved reviews.
It never starts inference, PDF acquisition or Zotero writes. Valid empty reports,
missing current reviews, malformed bundles and per-paper failures remain distinct.

The runner's bounded successful-stage cursor lives in its existing `state.json`;
complete bundles publish before that atomic pointer advances. Input/profile/code/
schema and fresh canonical review identity qualify reuse, not cached cohort order.
Ranking always sees the current whole cohort first. This is not concurrent exactly-once
execution, semantic freshness or measured token/time savings.

## Source-projection correction (runtime NEEDS_EVIDENCE)

```text
topic keywords -> claimed project fit       => no semantic warrant
implementation prose -> generic experiment => missing target/validation producer
review/card existence -> full-text count    => missing source/acquisition qualification
```

Source mechanisms are localized in `card.build_card`, `runner._records` and the
successful-stage cursor: topical membership was treated as usefulness, and a digest
or its absence was treated as acquisition evidence. Prospective controls in
`tests/test_research_feed_projection_evidence.py` bind the same candidates to absent,
abstract, wrong-identity, source-qualified, unavailable and failed-projection inputs.
Predicted old-source failures include invented positive uses/ideas, false full-text
cards/manual requests and card-count availability. These predictions are NOT executed
RED/GREEN; causal counterfactual/runtime validation remains NEEDS_EVIDENCE for parent.

The correction subtracts keyword-derived project uses and templated experiments;
`[]` plus NOT_ASSESSED notes/provenance distinguishes unknown from assessed no-fit.
Exact project GoalSummary evidence may survive without a synthesized use. Explicit
full-text basis and the existing current review/source binding qualify cards; source
availability, unqualified reviews and projection failures are separate observations.
The existing cursor hashes and projection marker invalidate legacy replay; original
bundles, candidate originals, ranking, triage, budgets and legacy evaluation math are
not rewritten. Nullable provider/model/schema/timestamp stay unknown; no triage
confidence is substituted for per-field confidence.

Compared options: retaining keyword heuristics is rejected (no semantic authority);
adding a judge/call is out of scope; bounded abstention is chosen pending the real
producer. Single-session trace/boundary critique finds no case-specific identifier
rule: source structure governs admission, not paper vocabulary. Independent review
and parent runtime checks remain required. Codex `compact.rs:665–747` illustrates
original/derived separation and `client_common.rs:23–65` separates input/instructions/
output schema; these are analogies, not scientific grounding rules. Existing GitHub
and arXiv recency148 metadata were read only; no code, model or framework was adopted.
Occam/Tesler subtract-first review removes false recommendations and assigns unknown
state visibility to the existing JSON/Markdown projection, not another UI or registry.

Separate producer proposal (not implemented): extend the SAME existing full-text
digest response with an optional source/project-bound mechanism, target component,
use kind, proposed change and concrete minimal validation experiment, or assessed
no-fit. Supply the declared versioned profile/project context at that existing call;
include schema/prompt/profile context in generation/cache identity and preserve
source/basis/model/provider/time/abstention provenance. Then project typed output;
no second summarizer/scorer/model call and no private #49 collector activation.

## Data required to close the evaluation gate

Capture 30–50 papers with, for each row:

- title, source URL/identifier, and the abstract used by the original triage;
- frozen machine score, priority, and summary from before any human decision;
- an independently authored inclusion label and rationale, kept out of model input;
- manually checked artifact availability where artifact precision is evaluated.

The existing evaluation test intentionally guards against a false green and
human-label leakage. No thresholds or labels were changed to make this fixture
pass. The product behavior and evaluation gate should remain distinct until the
source inputs and independent labels are available.
