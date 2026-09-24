# Selective-reading evidence for issues #9 and #15

This summary keeps the acceptance evidence reviewable without copying private
Zotero PDFs, paper titles, item keys, labels, or local file paths into the
repository. The original receipts remain in ignored `data/issue_runs/`.

## Issue #9 — selective reading

The 2026-08-27 evaluation used 12 real PDFs. Human expected actions and rationales
were recorded before inference; each paper was run once with the configured
`kather` / `sota` provider and model. The prior saved decisions on the same papers
are the baseline.

| Measure | Result |
|---|---:|
| Previous `read` decisions | 4 / 12 (33.3%) |
| New `read` decisions | 2 / 12 (16.7%) |
| Match to prewritten expected action | 10 / 12 (83.3%) |
| Designated decision-changing reads retained | 2 / 2 |
| Skim actions with a specific locator | 12 / 12 |
| Skip actions meeting the empty-read-target/value contract | 12 / 12 |
| Self-contained skip explanations | 12 / 12 |
| Digests with an estimated time | 10 / 12 |
| Inference failures | 0 |

The time estimate is optional, so the two null estimates are accepted unknowns.
The old/new comparison is against the previously stored decisions on these same
papers; it is not a new control-arm inference run.

## Issue #15 — idea, evidence, and writing friction

The 2026-08-27 evaluation used 13 real PDFs. Idea, scientific-concern, and
writing-friction labels were written before inference. The digest used one model
call per paper; no writing-specific call was added.

| Measure | Result |
|---|---:|
| Read precision | 1.00 |
| Idea-rescue recall | 1.00 |
| Exact three-class writing agreement | 6 / 13 (46.2%) |
| Writing agreement within one adjacent class | 12 / 13 (92.3%) |
| Non-low model friction outputs with concrete reasons | 100% |
| Papers labeled high friction by reviewers | 2 |
| Papers predicted high friction | 0 |
| Inference failures / retries | 0 / 0 |

The high-friction read cap did not activate in this sample because the model
predicted no paper as high friction. Its behavior is covered by deterministic
contract tests, not by this real-paper run. One high-friction, low-idea paper was
recommended for a skim instead of the reviewer-labeled skip; the digest did not
recommend a full read. Writing friction should therefore be treated as a coarse
review signal, not a calibrated three-class measurement.

## Reproduction and fingerprints

Focused verification on the current checkout:

```bash
uv run pytest -q tests/test_quality_review.py tests/test_reading_policy_eval.py \
  tests/test_paper_read_brief.py tests/test_review_fleet_propose.py \
  tests/test_note_render.py tests/test_assess_digest_retry.py \
  tests/test_faithbench_runner.py
```

Result: **112 passed**. This command checks the offline fixture gates, schema and
policy contracts, cap behavior, and rendered output. It does not repeat the
configured-provider PDF evaluations; doing that requires the local PDFs and the
prewritten human labels.

The following path-independent fingerprints identify each PDF/label/output set.
For each paper, its record contains the SHA-256 of the PDF bytes plus the
pre-inference label and relevant baseline/prediction fields. The dataset digest
is SHA-256 over the sorted canonical records. The receipt digest identifies the
complete local receipt, which also contains private paths and is not published.

| Issue | Papers | PDF/label/output set SHA-256 | Local receipt SHA-256 |
|---|---:|---|---|
| #9 | 12 | `8fb031a345b1339263ffc2a014998578566baf5e58b2d5a471f0b86c71768f76` | `4c4649fa0e15b66cc53b17a3aa8052ba36e10fc1e6bcd96227756dd372bb7a8a` |
| #15 | 13 | `c11bd7954699c4ef20c8236bfaa31a78fb353a3b6b908e2842b462b745bb8a37` | `62806f0cbab65569e82eb2e7dc6ced2fcae97a01ca4e71f8a71988400f131e9d` |
