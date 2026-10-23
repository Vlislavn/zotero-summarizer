# Opt-in interpretation experiment

```text
frozen benchmark + approved reference + family/version/time recipe
  -> A current / B caution / C same caution + optional AI draft
  -> existing digest verification -> independent optional scientific checks
  -> original-authority QA (four assigned cells per paper)
  -> saved/reopened record -> audited brief + Zotero-note HTML
  -> blinded source-based human judgments -> paper-paired report
```

This is experiment tooling, not evidence that cards help. Ordinary review/Q&A,
cache owners, default models and Add authorization are unchanged. Shared source
contract changes conservatively invalidate old currentness fingerprints; retained
receipts remain stored. No live twenty-paper study, native user write, human
acceptance or scientific benefit follows from synthetic protocol tests.

## Input and model admission

Reuse an existing Faithbench benchmark, its exact approved `.review.csv`, and
hash-named frozen texts in the selected project's `Settings.faithbench_dir/papers`.
Curate **at least twenty distinct held-out clinical/agent paper families**, each
with exactly four questions: answerable/unanswerable × neutral/leading. Retain
separate earlier tuning sources; tuning cannot supply evaluation questions.
Human approval is the semantic gate. Family/version/time declarations are not
cryptographically authenticated or automatically verified bibliographic facts.

The recipe is one strict JSON object (`services.faithbench._interpretation_inputs.Recipe`).
Use its JSON Schema for the complete contract; an incomplete example is not a run.
Required fields:

| Field | Meaning |
|---|---|
| `benchmark`, `benchmark_sha256`, `review_sha256` | Selected-project benchmark name and complete admitted byte hashes |
| `groups` | Every paper → family, source version, timezone-qualified publication time, tuning/evaluation split and clinical/agent domain |
| `stance` | Every item ID → neutral/leading; answerability comes from approved QA/trap type |
| `model_profiles` | map/reduce/qa → canonical hashes of actual resolved stage profiles |
| `runtime_conditions` | Explicit planned cache/memory/runtime conditions, distinct from observations |

Models reuse the current strategy's map/verifier stage (`feed` only for map/reduce,
otherwise `deep_review`), `deep_review` digest and `deep_review` QA. The existing
runtime resolver owns caching and the digest's `provider.thinking_on` override;
QA/verifier keep their distinct default control. Profile hashes use `_digest_audit.canonical_hash` on each
`ResolvedStage.model_dump(mode='json')`; no provider is constructed to compute them.
No model override, download, server start or API-key prompt is hidden in this command.
A profile hash/model name does **not** establish immutable remote weights.

Defaults are named recipe fields: twenty-paper minimum; 60,000-character QA/review
budget; 8,000-character maps; serial subcalls; 256 logical client calls per
paper/condition; seed49 and2,000 paper-cluster bootstrap samples. Local profiles
must stay serial. Limits are work-based; normal provider request timeouts remain
owned by selected Settings. Logical calls do not prove physical retry counts.
Project decision thresholds may be tightened, not weakened. Inclusive comparisons
use exact decimal thresholds: token means derive from the retained integer counts,
not rounded display means; latency and interval endpoints use their captured decimal
representations. Reports keep their original mean/median values. No epsilon or
rounding admits an observation above the cap; inconsistent displayed token means
refuse rather than overriding their count maps.

## Commands and consent

```bash
# Read-only admission, no provider/client construction or output publication.
zotero-summarizer faithbench interpretation --recipe data/faithbench/recipe.json
# Equivalent recipe selection: ZS_INTERPRETATION_RECIPE=data/faithbench/recipe.json

# Explicit, foreground execution; retain sensitive sources/prompts/raw completions.
zotero-summarizer faithbench interpretation --recipe data/faithbench/recipe.json --run --capture-local

# Export condition/model-free source/response packets and an UNFILLED label template.
zotero-summarizer faithbench interpretation --run-id <reported-uuid>

# Score independent human judgments; do not reuse agent-generated self-verdicts.
zotero-summarizer faithbench interpretation --run-id <reported-uuid> --labels <human-labels.json>
```

All commands accept `--project-root`. Use the repository's resource-safe foreground
protocol and coordinate any local-model run. The command does not impose an OS
RSS limit or supervise a separately owned model server. Do not stack local models,
GPU work or full builds. This change grants no live/private/model/native execution
authority merely because a recipe or old receipt exists.

## Records, failures and reopening

Explicit execution creates a private immutable attempt under
`data/faithbench/interpretation/<uuid>/`. Inputs and a complete assignment plan
precede provider construction. Each paper/condition has its saved review, four
question outcomes and full decoded/callback observations. Callback raw generation
text is captured before parser reduction/truncation rejection where the provider
exposes it; this is not raw HTTP transport. Missing callback usage stays unknown.
Aggregate provider usage is counted once, not once per generation. Failed questions
remain assigned failures, never successful abstentions or silently dropped rows.

Independent base acceptance precedes optional verification. Optional unavailable,
invalid and unsupported outcomes preserve the base and do not regenerate it.
Only scientific leaves/current referenced basis values are checked; origin/status
metadata cannot certify claims. Boolean false is retained. New optional checks
use strict booleans/indices without changing A's legacy verifier DTO.

A reopened qualified record reaches both actual renderers through one shared
Finding / Scope / Unproven section. Final brief bytes pass the existing audit
before publication. `zotero-note.html` is an HTML handoff only: no native database,
writer, consent, target identity or Add permission is supplied by an experiment.
Original quote/answer authority remains mandatory; a whole card uses unused QA
context budget only and is omitted explicitly when it does not fit.

The manifest is published last. Reopening checks exact planned inventory, hashes,
source/code/runtime identities and every assigned outcome. Partial attempts are
retained but cannot produce a complete report. A retry creates a new UUID; there
is no overwrite, automatic repair, last-row-wins replacement or implicit resume.
Unsigned consistency is not authentication or proof of semantic completeness.
Reports/blind packets are separate immutable siblings under `interpretation-reports`.

## Human judgments and decision

Give `blinded.json` to the review/QA raters and `cards.json` to separate card
raters: the former omits cards; independent random card IDs disclose no review-ID
mapping. Merge their independently filled template judgments without changing IDs.
Judgments bind to response hashes and normalized question TEXT, never position.
Card targets use normalized scientific TEXT plus exact source/evidence identity;
the packet supplies original text, selected passages, checks and source coordinates.
Identical claim text with different evidence remains separately judgeable.
Score answer correctness and causal/scope/recommendation overclaims; separately
score review overclaims, review/Add regressions and every retained scientific
card claim/evidence link. Empty judgments are not false/negative observations.

Optional operational judgments (`controlled_runtime`, `complete_token_accounting`,
`model_identity_verified`) require independently reviewed evidence files, referenced
by relative path and exact SHA-256 beside the label file. This is supplied human
review, not hardware/endpoint authentication by the scorer. Unknown observations
remain unknown and cannot establish the resource gate.

Reports retain failed/unjudged-question lower/upper overclaim bounds, supported
answer accuracy (abstentions wrong), overall accuracy, unnecessary abstentions,
failures, mean **and** median and domain/cell breakdowns. Seeded paired bootstrap
resamples papers, keeping all four cells together. Tokens and p95 elapsed time
cover generation plus four answers, excluding admission/publication/startup.

`GO_TO_LARGER_PILOT` is conditional on supplied independent judgments: ≥30% relative
and ≥5-point absolute reduction versus BOTH controls with positive improvement
intervals; ≤5-point supported-accuracy loss; no false retained card claims, invalid
links, review/Add regression or increased failures; observed tokens and p95
latency ≤1.25×A with complete operational evidence (token cap applies to each
paired paper, not just the cohort mean). Timing and token caps use their own
required observations: a known breach gives `NO-GO` even when the other resource
is unknown; GO requires both. A known correctness/resource
gate failure or conclusive benefit shortfall gives `NO-GO`; otherwise missing
observations, no baseline errors or inconclusive intervals give `INCONCLUSIVE`.
Defaults remain unchanged in every case. A conditional pilot recommendation is
not adoption, scientific superiority, human acceptance or issue closure.
