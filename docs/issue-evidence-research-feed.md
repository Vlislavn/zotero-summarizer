# Research-feed evaluation evidence for issue #12

The offline acceptance command currently exits nonzero:

```bash
uv run python tools/eval_research_feed.py --check
```

On this checkout it reports 30 rows, `inclusion_basis:
unscorable_missing_frozen_machine`, unavailable (`null`) shortlist precision,
recall, feed reading/card/artifact metrics, a separate 17-row cached-policy
agreement of `0.588`, and `passes: false` (exit 1). A wiring projection is not
a measured production result; previously reported 0/1 values were misleading.

## Why the result cannot validate the product

`tools/research_feed_fixture.json` contains paper titles, human inclusion and
must-not-miss labels, decision labels, project labels, and verified repository
URLs for some rows. It does not contain abstracts, canonical source identifiers,
or the frozen pre-human triage evidence used in production (`composite_score`,
`reading_priority`, and the stored model summary).

The evaluator can still project the fallback path with an empty abstract and
`triage_candidate(candidate, None, profile)`, but returns `null` rather than
measured inclusion scores until **every** row supplies independent pre-human
`frozen_machine` inputs: authoritative `source_url`, nonempty `abstract`,
finite 1–5 `composite_score`, valid `reading_priority`, and a model `summary`
object. Present-but-null fields are **also unscorable**, not measured zeros;
malformed non-null values fail loud. This input
never includes human `decision` or labels. A real zero from supplied machine
inputs remains distinguishable from an unscorable fixture; top-K then follows
production score/confidence/title order with a stable ID tie-break.

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

The separate 17-row `reading_policy_fixture_v2.json` produces `0.588`
cached-policy agreement, reported under `reading_policy_fixture_agreement`,
and does not provide frozen research-feed model inputs.
It must not be used to claim that issue #12's paper-inclusion benchmark passes.

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
