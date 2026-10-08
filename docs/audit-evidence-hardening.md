# Audit evidence hardening — #25 local acceptance record

## Local acceptance — 2026-10-08 (prepared before publication)

All five formerly partial GitHub #25 medium findings (A111, A124, A136, A142,
A176) are verified complete in the current code: **42/42 medium findings**.
This is a local acceptance report prepared before publication; remote review,
merge, and issue-closure state is maintained in the GitHub issue/PR, not copied
into this report. The local issue-body draft is
`data/a111-release-gates/issue25-body.md`; this pass did not edit GitHub and
asserts no PR number. Suggested title: **functional audit Medium findings
complete (42/42)**.

| Finding | Verified resolution and limit |
|---|---|
| **A111** | Article acquisition removes opaque print/body materialization and uses bounded main-document capture, complete admitted `document.body` text collection, and a bounded text-only PDF sink. Over-budget, mutated, invalid, or incomplete work fails closed; there is no prefix success. Live synthetic-origin run: 18 passed/0 skipped, 217 CDP calls/0 `Page.printToPDF`; this is not a Chromium/OS RSS guarantee. |
| **A124** | Two batched calls check section summaries against the same first 700 source characters (up to 24 nonempty sections); unsupported summaries are omitted. Focused suite: 23 passed. No live semantic-accuracy claim. |
| **A136** | Only affirmative whole-paper figure-count wording uses metadata; other scopes route to grounded Q&A. Focused Q&A/freshness suite: 39 passed. No live natural-language accuracy claim. |
| **A142** | Proposal reads/upserts validate typed rows, skip malformed entries while retaining valid neighbors, preserve/quarantine corrupt whole files, and calibration uses valid proposal/human pairs. Focused strict-boundary suite: 74 passed. |
| **A176** | All admitted text is partitioned into contiguous 6,000-character windows; 32 default/256 maximum, all-paper preflight before builder calls, selected-project `.env` precedence and frozen effective budget. Focused FaithBench suite: 93 passed. This is a work bound, not an accuracy claim. |

### Final release receipts

Primary receipts: `data/a111-release-gates/summary.md` and
`data/a111-live-final-provenance/summary.md`.

| Gate | Actual result |
|---|---|
| Pre-commit | **9/9 hooks passed** |
| Focused article/browser/PDF/egress tests | **231 passed, 30 skipped** |
| Full forked suite | **4,246 passed, 39 skipped, 0 failed** |
| Full serial suite | **4,244 passed, 39 skipped, 2 failed**; exact baseline set, no new failure |
| CLI smoke/help | Exit 0; `route_count: 117` |
| Live synthetic-origin browser | **18 passed, 0 skipped**; **217 CDP calls**, **0 `Page.printToPDF`**; Chrome 154 and bundled Chromium 148 |

Serial baseline failures are
`tests/test_review_fleet.py::test_read_all_quarantines_corrupt_file_without_losing_bytes`
and `tests/test_startup_boundaries.py::test_startup_rss_failure_is_logged_without_unretrieved_task`.
A captured 4,096-byte genuine PDF was byte-identically retained in the raw-URL
cache under an 8-byte text budget, without a text-writer call or snapshot; its
provenance remains `web_article=False`, `source="browser"`. Only equality with the
canonical `article_snapshot_path(source_url, cache_dir)` under
`article-snapshots/` marks a text snapshot `web_article=True`. Legacy root-level
cache files are retained and not reclassified. The full-Unicode fixture round-trip
passed; Greek/Russian glyphs were visually read as clean. Article PDFs are a
bounded text projection of the admitted body DOM, not the original visual page.

The source, page, DOM-work, and output budgets fail closed instead of clipping a
prefix; built-in Helvetica/`cjk` fonts are used and unsupported glyphs fail closed.
Sampled live guard values were peak tree RSS **726,499,328 bytes**, minimum free
RAM **46%**, and swap growth **0 bytes**. These are run observations, not a
Chromium/OS memory ceiling. A124/A136 semantic accuracy was not measured.

## Historical final V2 checkpoint — 2026-10-08 (before A111 closure)

**Scope at that checkpoint:** A124, A136, A142 and A176; A111 remained outside
that repair. The earlier V2 runner receipt is `data/audit-final-v2/summary.md`;
the earlier pending serial/selected-`.env` checkpoint remains historical. Source
comparisons used base `7a9ffc7`.

```text
original paper / persisted proposal
  ├─ A124: section prefix → batched summary → batched support check
  │         same first 700 chars; at most 24 sections; no supported result → no summary
  ├─ A136: explicit whole-paper figure count → truthful figure-only metadata
  │         scoped, table/combined, or uncertain wording → grounded Q&A
  ├─ A142: proposal row → ProposedVerdict validation → skip invalid row, retain valid neighbors
  │         corrupt whole file → preserve/quarantine as before
  └─ A176: complete text → contiguous 6,000-char windows → builder
            32-window default, 256 maximum; over-budget input → fail before any builder call
```

## Earlier V2 implementation and evidence status

| Finding | Current source receipt | Status and limit |
|---|---|---|
| **A124** | `zotero_summarizer/services/library/_paper_section_summaries.py:21-23,89-119,122-164` batches generation, then a second support-verifier batch checks the same selected section prefixes (700 chars, at most 24 nonempty sections). It requires ordered, unique section mappings and a source quote for supported outcomes; unsupported summaries are omitted. | **Fixed structurally:** focused section-summary suite: 23 passed. This is bounded structural support verification, not measured live semantic accuracy. |
| **A136** | `zotero_summarizer/services/library/qa.py:41-55,158-180` admits only explicit whole-paper count wording to metadata; whole-paper figure answers use `figures_count` and say figures only. Scoped, table-only, combined figure/table, and unmatched wording falls through to grounded Q&A. | **Fixed:** focused Q&A + freshness suite: 39 passed. This verifies routing and truthful metadata labels, not live natural-language accuracy. |
| **A142** | `zotero_summarizer/services/library/review_fleet/verdict_store.py:38-76` strictly validates proposal ingress, reads, and upserts against `ProposedVerdict`, skipping malformed rows while retaining valid neighbors; whole-file corruption keeps byte-preserving quarantine. Currentness rejects invalid/stale proposals, and `quality_calibration.py:45-80` forms pairs only from valid proposals and human labels. | **Fixed:** focused strict-boundary suite: 74 passed. Calibration uses valid human/proposal pairs only. |
| **A176** | `zotero_summarizer/services/faithbench/_build_qa.py:62-76,251-277` partitions all admitted text into contiguous 6,000-character windows and preflights every paper before the first builder inference call. `_constants.py:33-56` defines configured default 32 and maximum 256; `cli/_faithbench.py:25-37,365-370` resolves CLI > shell environment > selected project's `.env` > default and records the effective limit in metadata. | **Fixed:** focused FaithBench suite: 93 passed. Raising the configured limit increases builder-call cost; this is a per-paper work bound, not an accuracy result. |
| **A111** | No change in this scope. | **Partial / open:** bounded transfer does not bound memory used inside native browser rendering. |

The 2026-10-05 audit counts remain historical. Final V2 verification is complete: focused, selected-project `.env`, CLI/parser/smoke, pre-commit, forked, and serial receipts are recorded below. The serial run retains exactly the two unchanged baseline failures and has no new failures. A111 is the only remaining partial finding in this four-finding repair scope. A124's scripted verifier and A136's routing checks are not measured live semantic-accuracy results.

## Focused test selection used by the runner

```bash
KMP_DUPLICATE_LIB_OK=TRUE PYTHONPATH="$PWD" .venv/bin/python -m pytest -q --forked \
  tests/test_paper_section_summaries.py tests/test_library_qa.py \
  tests/test_review_fleet.py tests/test_review_fleet_store.py \
  tests/test_quality_calibration.py tests/test_review_identity.py \
  tests/test_audit_evidence_acceptance.py tests/test_project_state_paths.py \
  tests/test_faithbench*.py
```

The canonical runner receipt is `data/audit-final-v2/summary.md`; this documentation-only update did not rerun tests or hooks.

## Source provenance and recency

The original-source distinction and fail-closed ordering are supported patterns, not evidence of this app's quality: Codex retains original messages separately from a compacted summary and marks omitted context incomplete (`codex-rs/core/src/compact.rs#L559-L574,#L662-L737`, local revision `67a709665ac7b50311b93e32612c9a8281684787`); ARE evaluates hard checks before soft checks (`are/simulation/validation/tool_judge.py#L591-L631`, local revision `a3bc20c4580c6fe5d7528fd9b2a8da778f5ea395`). Neither source establishes semantic entailment or count-scope accuracy here. FActScore (`arXiv:2305.14251v1`) and VeriScore (`arXiv:2406.19276v1`) are prior claim/evidence precedents, not product evaluation.

A keyless, date-sorted public-source sweep was inspected on 2026-10-08. [`shekhar871/ayurveda-ai`, `59fdcd32`](https://github.com/shekhar871/ayurveda-ai) is MIT-licensed; `src/agents/citation_verifier.py#L14-L23` checks citation identifiers/ranges, not claim entailment. [`Aletheore/Aletheore`, `ef461790`](https://github.com/Aletheore/Aletheore) has repository license `NOASSERTION`; `src/aletheore/citation_verifier.py#L40-L71` is a citation parser, not paper-claim support. Recent preprints [`arXiv:2609.14245v1`](https://arxiv.org/abs/2609.14245v1), [`2608.30145v1`](https://arxiv.org/abs/2608.30145v1), and [`2608.10627v1`](https://arxiv.org/abs/2608.10627v1) discuss source-span attribution, scope normalization, and decomposition risks. They inform design only; peer review/full-text evidence was not established here, and none is a measured Zotero result.

## Historical runner receipt — 2026-10-08 final V2 (before A111 closure)

Historical first checkpoint (superseded, retained for chronology): focused eight-module selection **229 passed**; pre-commit **9/9 hooks passed**; preliminary forked suite **4,128 passed, 32 skipped, 0 failed**; preliminary serial suite **4,120 passed, 32 skipped, 8 failed** (two baseline plus six logging assertions); selected-project `.env` recheck was then pending. CAPA identified the logging assertion issue; the corrected final run below is authoritative.

| Receipt | Final V2 result |
|---|---|
| Focused selection | **406 passed**, including the verdict-store logging seam and every `tests/test_faithbench*.py` module |
| Selected-project `.env` assertions | **8 passed**, covering selected-root resolution before provider setup, preflight, invalid values, and shell/CLI precedence |
| `.venv/bin/pre-commit run --all-files` | Exit 0; all **9 hooks passed** |
| CLI/parser and smoke | Help confirms window range **1–256** and CLI > `ZS_FAITHBENCH_QA_MAX_BUILDER_WINDOWS` > default 32; separate selected-project `.env` assertions passed. Smoke returned `ok: true`, `route_count: 117`. Integration import/root verified; no live model/Zotero calls. |
| Full forked suite | **4,135 passed, 32 skipped, 0 failed** (4,167 collected) |
| Full serial suite | **4,133 passed, 32 skipped, 2 failed** (4,167 collected); exact baseline failed-node set, no new failures |
| Live semantic accuracy (A124/A136) | Not measured; do not infer from scripted tests |
| A111 native-render memory bound | Open; outside this scope |

The unchanged serial baseline failures are `tests/test_review_fleet.py::test_read_all_quarantines_corrupt_file_without_losing_bytes` and `tests/test_startup_boundaries.py::test_startup_rss_failure_is_logged_without_unretrieved_task`.
