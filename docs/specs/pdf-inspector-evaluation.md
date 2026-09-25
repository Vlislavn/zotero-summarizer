# PDF Inspector evaluation and lifecycle specification

Status: proposed
Owner: Zotero Summarizer
Decision type: reversible dependency and parser-routing decision

## Summary

Evaluate `firecrawl/pdf-inspector` as a fast structured parser for native-text PDFs. It must not replace OCR or figure extraction by assumption. Adoption is gated by a repository-specific benchmark, and continued use is gated by explicit operational and quality criteria.

The intended architecture is:

```text
PDF
  -> classify and extract with pdf-inspector when safe
  -> route scans, mixed documents, broken encodings, and low-quality output to Docling/OCR
  -> retain PyMuPDF as the metadata, hyperlink-annotation, figure-region, and emergency fallback path
```

No production default should change until the acceptance gate below is passed.

## Problem

The current light PDF path uses PyMuPDF block extraction and heuristic section detection. This is inexpensive but may lose reading order in multi-column papers and flatten tables. Docling provides higher-fidelity layout parsing but is substantially heavier.

`pdf-inspector` may fill the gap: local, fast extraction for PDFs that already have a usable text layer, plus classification that can prevent unnecessary OCR. The project should adopt it only when it improves downstream reading quality without creating an unreliable native dependency or permanent parser complexity.

## Scope

In scope:

- native-text, mixed, scanned, and image-based PDF classification;
- text and Markdown extraction;
- headings, reading order, and tables;
- parser routing and fallback behaviour;
- downstream impact on ask-the-paper and deep review;
- packaging, installation, latency, crashes, and maintenance burden;
- explicit adoption, rollback, and removal criteria.

Out of scope for the first decision:

- replacing figure-region extraction;
- replacing hyperlink annotation harvesting;
- normalizing references into a bibliographic schema;
- page-level stitching between several parsers;
- deleting Docling or OCR support;
- treating the upstream benchmark as sufficient evidence.

## Proposed integration boundary

PDF engines must expose one normalized internal contract rather than leaking backend-specific objects:

```python
{
    "title": str,
    "authors": str,
    "keywords": list[str],
    "n_pages": int | None,
    "sections": list[dict],
    "full_text": str,
    "link_uris": list[str],
    "references_count": int,
    "parser": str,
    "parser_version": str | None,
    "parser_warnings": list[str],
    "pages_needing_ocr": list[int],
}
```

The adapter belongs under `integrations/`; routing belongs in the library service. The default remains unchanged during evaluation.

Suggested backend modes:

```text
auto | pdf_inspector | docling | pymupdf
```

Initial `auto` policy after acceptance:

1. Classify using pdf-inspector.
2. Use pdf-inspector output for high-confidence native-text PDFs that pass output sanity checks.
3. Route scanned/image-based PDFs to Docling with OCR.
4. Route mixed PDFs, broken encodings, extraction failures, and failed sanity checks to Docling or PyMuPDF according to configured availability.
5. Preserve PyMuPDF harvesting of link annotations and figure regions independently of the text backend.

Page-level hybrid parsing is deferred until whole-document routing has measured failures that justify the extra complexity.

## Benchmark corpus

Use at least 60 PDFs sampled from the real Zotero workload. Do not tune or decide on the upstream benchmark alone.

Minimum strata:

- 15 two-column arXiv or conference papers;
- 10 biomedical journal articles from several publishers;
- 10 papers with large or multi-page tables;
- 5 supplementary-method PDFs;
- 5 scanned or image-only articles;
- 5 mixed text-and-scan PDFs;
- 5 documents with unusual fonts, CID encoding, or known extraction damage;
- 5 long reports or reviews over 50 pages.

Record the corpus manifest and document hashes. Avoid committing copyrighted PDFs; store only identifiers, hashes, manually assigned strata, and derived measurements.

## Comparators

Required:

- current PyMuPDF path;
- pdf-inspector;
- Docling with OCR disabled for native-text documents;
- Docling with OCR enabled for scans where applicable.

Optional:

- GROBID or another scientific parser when a future decision concerns structured metadata rather than general Markdown.

## Measurements

### Parser-level quality

For every document:

- reading-order correctness;
- abstract, methods, results, discussion, and limitations completeness;
- heading preservation;
- table preservation and cell-order correctness;
- duplicate or omitted paragraphs;
- replacement characters and broken encoding;
- page-header/footer contamination;
- output token count;
- extraction latency;
- peak resident memory;
- exception, crash, and timeout rate.

Human scoring should use blinded paired review on a fixed rubric. At least 20 documents must receive duplicate review to estimate reviewer agreement.

### Downstream quality

Run a fixed question set on at least 20 papers using identical model, prompt, context budget, and decoding settings.

Measure:

- answer correctness;
- evidence coverage;
- unsupported-claim rate;
- abstention accuracy;
- citation or quoted-passage recall;
- number of questions made unanswerable by parser omissions;
- deep-review completeness for methods and limitations.

### Operational quality

Test on supported environments, including the primary Apple Silicon development machine:

- clean `uv sync` installation;
- wheel availability without a local Rust toolchain;
- cold and warm startup;
- parallel processing under the expected worker count;
- malformed and adversarial PDFs;
- deterministic or acceptably stable output across repeated runs;
- licence compatibility and release cadence.

## Acceptance gate: when pdf-inspector may become the native-text default

All mandatory criteria must pass:

1. **Quality:** on native-text PDFs, pdf-inspector is non-inferior to Docling and materially better than the current PyMuPDF path for reading order or table preservation. The paired 95% confidence interval must exclude a degradation greater than 2 percentage points on the primary human quality score.
2. **Downstream:** it does not increase unsupported answers by more than 1 absolute percentage point and reduces parser-caused unanswerable questions or improves evidence recall by at least 5 relative percent versus PyMuPDF.
3. **Reliability:** document-level failure rate is below 1% on the benchmark corpus, with every failure handled by an automatic fallback rather than terminating the paper-read job.
4. **Performance:** median native-text extraction is at least 3x faster than Docling on the primary machine, or saves at least 500 ms per document while meeting the same quality gate.
5. **Packaging:** supported platforms have installable wheels through the locked dependency workflow. Requiring users to install Rust is a rejection unless the dependency remains developer-only.
6. **Observability:** parser choice, version, latency, warnings, OCR routing, and fallback reason are recorded without storing copyrighted full text in telemetry.
7. **Rollback:** one configuration change restores the previous parser policy, and cached outputs retain parser/version provenance.
8. **Maintenance:** the adapter and routing implementation remain below the repository LOC limit, include contract tests, and do not duplicate figure or link extraction logic.

Passing the gate permits a staged rollout, not immediate deletion of existing parsers.

## Staged rollout

### Stage 0 — spec and harness

- land this specification;
- create a reproducible benchmark command;
- define the corpus manifest and annotation rubric;
- record baseline PyMuPDF and Docling results.

Exit: the benchmark can be rerun from a clean checkout and produces machine-readable results.

### Stage 1 — optional adapter

- add `pdf-inspector` as an optional dependency;
- implement the normalized adapter;
- keep it disabled by default;
- add contract, malformed-PDF, and fallback tests.

Exit: explicit `pdf_inspector` mode works without affecting existing users.

### Stage 2 — shadow evaluation

- run pdf-inspector alongside the selected production parser on a bounded local sample;
- retain only metrics, hashes, parser provenance, and compact error diagnostics;
- do not duplicate user-facing outputs.

Exit: acceptance criteria are evaluated on the frozen corpus and shadow sample.

### Stage 3 — guarded `auto`

- enable for high-confidence native-text PDFs only;
- retain automatic fallback;
- expose parser and fallback reason in diagnostics;
- review failure and regression metrics after 100, 500, and 2,000 documents.

Exit: no rollback threshold is crossed, and the measured benefit persists outside the benchmark corpus.

### Stage 4 — default decision

Adopt as native-text default only after Stage 3. Keep Docling/OCR and PyMuPDF fallback until the separate retirement criteria are met.

## Rollback thresholds

Immediately disable pdf-inspector in `auto` and return to the previous default when any of these occurs:

- unsupported-answer rate increases by at least 2 absolute percentage points over a rolling evaluable sample of 100 papers;
- parser-caused missing evidence exceeds the previous backend by at least 5 absolute percentage points;
- extraction/fallback failure exceeds 2% over 100 documents;
- process crashes, memory-safety concerns, or malformed-PDF vulnerabilities are observed;
- p95 latency is worse than the previous native-text backend for three consecutive benchmark runs;
- a dependency release breaks supported installation and cannot be pinned safely;
- output changes invalidate cached evidence locations without a migration or provenance-aware reprocessing path.

Rollback does not automatically remove the adapter. It returns the dependency to experimental status and opens a root-cause decision.

## Retirement criteria: when to stop using pdf-inspector

The project should remove pdf-inspector, rather than preserve it indefinitely, when one of the following decisions is supported by evidence.

### A. It never earns adoption

Remove the adapter and optional dependency when, after the benchmark and one remediation iteration:

- it fails any mandatory acceptance criterion; and
- there is no unique capability that is both used and superior to existing backends; or
- installation requires an unsupported native toolchain; or
- maintaining the adapter costs more than the measured latency or quality benefit.

Timebox: decide within two implementation PRs or 30 days after the benchmark harness is merged, whichever comes first.

### B. Another backend subsumes it

Remove pdf-inspector when another maintained backend meets all of these on the frozen and current corpora:

- equal or better primary quality score within the 2-point non-inferiority margin;
- equal or lower unsupported-answer rate;
- no worse than 20% on median latency, or the absolute difference is under 200 ms;
- supports the required classification/routing signals;
- simpler packaging and at least 30% less PDF-routing code or one fewer native dependency.

The replacement must pass the same staged rollout and rollback process.

### C. Its marginal value disappears

Remove it when, for two consecutive quarterly evaluations or two consecutive upstream upgrades:

- fewer than 5% of processed documents receive a better route or materially better output because of pdf-inspector; and
- disabling it changes downstream quality by less than 1 absolute percentage point; and
- its presence still adds release, security, platform, or cache-compatibility work.

### D. Maintenance or supply-chain risk becomes unacceptable

Remove or disable it when:

- no compatible release is available for two consecutive supported Python versions;
- critical vulnerabilities remain unpatched for 30 days after disclosure;
- the project is archived or has no substantive maintenance for 12 months while blocking required upgrades;
- licence terms become incompatible;
- deterministic crashes or unsafe parsing cannot be contained by process isolation and fallback.

### E. The product no longer needs local PDF parsing

Remove it if Zotero Summarizer changes architecture so that canonical structured text is supplied by Zotero, a trusted institutional service, or a single validated scientific-document backend, and local native-text parsing no longer improves privacy, latency, cost, or quality.

## Retirement procedure

A removal PR must:

1. attach the comparison report supporting one retirement criterion;
2. switch `auto` away from pdf-inspector before removing the dependency;
3. preserve reading of existing cached records with `parser=pdf-inspector`;
4. invalidate or reprocess only caches whose content contract changed;
5. delete adapter-specific configuration, tests, documentation, and lockfile entries;
6. verify that figure extraction, link harvesting, OCR, and fallback behaviour remain covered;
7. record the decision in the project decision log.

Do not delete provenance values from persisted records. Historical outputs must remain attributable to the parser and version that produced them.

## Decision schedule

- At merge of this spec: no adoption decision.
- After Stage 0 benchmark: adopt for Stage 1, reject, or request exactly one remediation iteration.
- Within 30 days of Stage 0 completion: decide whether pdf-inspector proceeds to shadow mode or is removed.
- After 100, 500, and 2,000 guarded documents: review rollback thresholds.
- Quarterly while it is an active default: rerun the frozen regression subset and reassess marginal value and retirement criteria.
- On every major pdf-inspector, Docling, PyMuPDF, Python, or packaging-platform upgrade: run the compact regression subset before updating the lockfile.

## Required deliverables before adoption

- benchmark harness and documented command;
- corpus manifest with hashes and strata;
- human-review rubric;
- machine-readable parser and downstream results;
- adapter contract tests;
- malformed-PDF tests;
- fallback and rollback tests;
- packaging matrix;
- decision report stating `adopt`, `continue experiment`, or `remove`.

## Open questions

- Does the published Python package provide wheels for every supported platform, or does it require local compilation?
- Which Python fields expose confidence, page count, encoding warnings, and pages requiring OCR in the pinned release?
- Does Markdown preserve evidence locations sufficiently for current quote/citation UX?
- Is whole-document mixed-PDF fallback adequate, or does the real corpus justify page-level OCR stitching?
- Should the parser run in a subprocess for malformed-PDF isolation?

These questions must be answered by the harness or adapter spike, not by assumptions from the upstream README.
