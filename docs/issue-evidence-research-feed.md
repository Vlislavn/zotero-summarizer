# Research-feed evaluation evidence for issue #12

The offline acceptance command currently exits nonzero:

```bash
uv run python tools/eval_research_feed.py --check
```

On this checkout it reports 30 rows, shortlist precision `0.0`, must-not-miss
recall `0.0`, read/skim/skip agreement `0.588`, artifact availability accuracy
`1.0`, reported code-link precision `1.0`, no fabricated URLs, and
`passes: false`.

## Why the result cannot validate the product

`tools/research_feed_fixture.json` contains paper titles, human inclusion and
must-not-miss labels, decision labels, project labels, and verified repository
URLs for some rows. It does not contain abstracts, canonical source identifiers,
or the frozen pre-human triage evidence used in production (`composite_score`,
`reading_priority`, and the stored model summary).

The evaluator constructs every candidate with an empty abstract and calls
`triage_candidate(candidate, None, profile)`. Without a production row, that
function conservatively defaults the score to `1`; therefore the observed zero
selection/recall does not measure production triage quality. Passing each
human `decision` as the row input would leak the answer because those labels are
the evaluator's outcomes. Inventing scores or summaries would manufacture the
missing model predictions.

Exact-title lookup can recover some independent public abstracts, for example
[SwarmWorld](https://arxiv.org/abs/2608.26081),
[GTA-RAG](https://arxiv.org/abs/2608.22479), and
[When Stale Constraints Go Unchecked](https://arxiv.org/abs/2608.25553). The
fixture does not identify all 30 rows with canonical URLs/IDs, and abstracts
alone would still not supply the frozen production triage outputs required by
this projection evaluator.

The separate 17-row `reading_policy_fixture_v2.json` produces only `0.588`
read/skim/skip agreement and does not provide frozen research-feed model inputs.
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
