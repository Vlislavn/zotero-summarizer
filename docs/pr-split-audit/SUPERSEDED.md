# Superseded original variants

```text
original source ──> immutable public snapshot
              └──> current canonical owner (not a duplicate implementation)
```

These are explicit dispositions, not missing-file fallbacks or claims of full API/behavioral parity. Original bytes remain at commit `34371710619b647a778ea89db4125d24874cf5ac`. The original oracles are separately retained for review; no assertion is rewritten to manufacture compatibility.

## `frontend/src/components/paper/RelatedLibrary.jsx`

Older combined Related renderer is replaced by the current saved-library and explicit discovery controls in RelatedPapers. Do not add a second Related authority.

- Review owners: #148, #153, #200
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/frontend/src/components/paper/RelatedLibrary.jsx)
- Current provenance: [`frontend/src/components/paper/RelatedPapers.jsx`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/frontend/src/components/paper/RelatedPapers.jsx) (Git blob `e4cb75fc152544aec3e31af031d6755c7762124f`).
- Current provenance: [`zotero_summarizer/services/library/related.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/services/library/related.py) (Git blob `06db2450170139c5ca7dc867fd66c06d3e0a14dc`).

## `frontend/src/components/paper/fixtures/discoveryChannelCandidates.json`

Older derived UI fixture is not a production authority. Current discovery uses the ordinary-wire fixture and untrusted input contracts; original related/discovery oracles are retained separately in 190.

- Review owners: #190, #200
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/frontend/src/components/paper/fixtures/discoveryChannelCandidates.json)
- Current provenance: [`tests/fixtures/discovery-ordinary-wire.json`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/tests/fixtures/discovery-ordinary-wire.json) (Git blob `bfe4ace0d115aa88b998ac010cb514ffe554e115`).
- Current provenance: [`frontend/src/components/paper/RelatedPapers.discovery.test.jsx`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/frontend/src/components/paper/RelatedPapers.discovery.test.jsx) (Git blob `f288d4b72b36bf3bb80fb22f96ad844ff2ec3bc0`).

## `frontend/src/utils/searchSort.js`

The added question_evidence sort mode belongs to the older UI variant. Current opt-in policy orders session candidates on the server; recommended mode preserves that order. Do not restore a competing UI promotion choice.

- Review owners: #147, #191
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/frontend/src/utils/searchSort.js)
- Current provenance: [`zotero_summarizer/services/search/_question_policy.py`](https://github.com/Vlislavn/zotero-summarizer/blob/3ee4eec51eef007365d2ee71bed25949d29447e8/zotero_summarizer/services/search/_question_policy.py) (Git blob `ef7503f9331f0a010c097cb3b96d684e88ebecb8`).
- Current provenance: [`frontend/src/pages/Search.jsx`](https://github.com/Vlislavn/zotero-summarizer/blob/3ee4eec51eef007365d2ee71bed25949d29447e8/frontend/src/pages/Search.jsx) (Git blob `bdf44449998b6e253cfc1e28def1f260601c8617`).

## `zotero_summarizer/integrations/arxiv.py`

Retire the context-local source-observer prototype. Current federation uses explicit request receipts, with authoritative OpenAlex outcomes; other empty channels remain unknown. Full old per-provider status parity is not claimed.

- Review owners: #153, #200, #190
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/integrations/arxiv.py)
- Current provenance: [`zotero_summarizer/services/search/federate.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/services/search/federate.py) (Git blob `22f214289b2cee5524f1e01c79349edb11efb327`).
- Current provenance: [`zotero_summarizer/integrations/openalex.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/integrations/openalex.py) (Git blob `00044c925a38e985ccac56a9053956aa9635ca69`).

## `zotero_summarizer/integrations/crossref.py`

Retire the context-local source-observer prototype. Current federation uses explicit request receipts, with authoritative OpenAlex outcomes; other empty channels remain unknown. Full old per-provider status parity is not claimed.

- Review owners: #153, #200, #190
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/integrations/crossref.py)
- Current provenance: [`zotero_summarizer/services/search/federate.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/services/search/federate.py) (Git blob `22f214289b2cee5524f1e01c79349edb11efb327`).
- Current provenance: [`zotero_summarizer/integrations/openalex.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/integrations/openalex.py) (Git blob `00044c925a38e985ccac56a9053956aa9635ca69`).

## `zotero_summarizer/integrations/europepmc.py`

Retire the context-local source-observer prototype. Current federation uses explicit request receipts, with authoritative OpenAlex outcomes; other empty channels remain unknown. Full old per-provider status parity is not claimed.

- Review owners: #153, #200, #190
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/integrations/europepmc.py)
- Current provenance: [`zotero_summarizer/services/search/federate.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/services/search/federate.py) (Git blob `22f214289b2cee5524f1e01c79349edb11efb327`).
- Current provenance: [`zotero_summarizer/integrations/openalex.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/integrations/openalex.py) (Git blob `00044c925a38e985ccac56a9053956aa9635ca69`).

## `zotero_summarizer/integrations/semantic_scholar.py`

Retire the context-local source-observer prototype. Current federation uses explicit request receipts, with authoritative OpenAlex outcomes; other empty channels remain unknown. Full old per-provider status parity is not claimed.

- Review owners: #153, #200, #190
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/integrations/semantic_scholar.py)
- Current provenance: [`zotero_summarizer/services/search/federate.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/services/search/federate.py) (Git blob `22f214289b2cee5524f1e01c79349edb11efb327`).
- Current provenance: [`zotero_summarizer/integrations/openalex.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/integrations/openalex.py) (Git blob `00044c925a38e985ccac56a9053956aa9635ca69`).

## `zotero_summarizer/integrations/search_outcomes.py`

The ContextVar observer is an older alternative to explicit request outcomes, not an additional active authority. Its non-OpenAlex status granularity is not silently claimed for the current implementation.

- Review owners: #153, #200, #190
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/integrations/search_outcomes.py)
- Current provenance: [`zotero_summarizer/services/search/federate.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/services/search/federate.py) (Git blob `22f214289b2cee5524f1e01c79349edb11efb327`).
- Current provenance: [`zotero_summarizer/integrations/openalex.py`](https://github.com/Vlislavn/zotero-summarizer/blob/f9dc3d19136eef37affc805199ce33c77aad0fbb/zotero_summarizer/integrations/openalex.py) (Git blob `00044c925a38e985ccac56a9053956aa9635ca69`).

## `zotero_summarizer/services/library/_deep_review_context.py`

Default review context remains in the existing deep_review owner. The old live/private interpretation-schema context extension is replaced by the explicit frozen experiment, not resurrected as a parallel live pipeline. Original context/interpretation oracles are retained in 187 without a compatibility claim.

- Review owners: #158, #187
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/services/library/_deep_review_context.py)
- Current provenance: [`zotero_summarizer/services/library/interpretation.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/interpretation.py) (Git blob `b3aa4045bb6d9f98999cce103055e0f02dbaffd9`).
- Current provenance: [`zotero_summarizer/services/library/deep_review.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/deep_review.py) (Git blob `314aea80e7d9c7e23a588cd6246f474864f999cf`).

## `zotero_summarizer/services/library/_interpretation_view.py`

Older private/live schema projection is replaced by qualification of frozen experimental records and their HTML/Q&A projections. The private schema API is not restored; the original tests remain reviewable in 187.

- Review owners: #158, #187
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/services/library/_interpretation_view.py)
- Current provenance: [`zotero_summarizer/services/library/interpretation.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/interpretation.py) (Git blob `b3aa4045bb6d9f98999cce103055e0f02dbaffd9`).
- Current provenance: [`zotero_summarizer/services/library/_interpretation_html.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/_interpretation_html.py) (Git blob `e64db0fef3f2408123bf966f702d30a2a897aa83`).
- Current provenance: [`zotero_summarizer/services/library/_interpretation_qa.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/_interpretation_qa.py) (Git blob `660f5ab3aa168c026f57cb0a7d486b325f210303`).

## `zotero_summarizer/services/library/_paper_read_meta.py`

These original changes restructure context assembly for the old private interpretation-schema variant. Current paper-Q&A evidence identity remains in 141; optional experimental context belongs to frozen-record Q&A in 158. No live/private schema parity is claimed.

- Review owners: #141, #158, #187
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/services/library/_paper_read_meta.py)
- Current provenance: [`zotero_summarizer/services/library/_interpretation_qa.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/_interpretation_qa.py) (Git blob `660f5ab3aa168c026f57cb0a7d486b325f210303`).
- Current provenance: [`zotero_summarizer/services/library/qa_context.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/qa_context.py) (Git blob `f8b5dc3934b390fbac7085f07a6d4dd6a3055edb`).

## `zotero_summarizer/services/library/qa.py`

Do not reintroduce the older interpretation_schema branch into normal ask_paper. Current experimental Q&A has an explicit frozen-record entry point; normal checkpoint/evidence behavior remains owned by 141. Older oracle contracts are retained in 187, not asserted compatible.

- Review owners: #141, #158, #187
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/services/library/qa.py)
- Current provenance: [`zotero_summarizer/services/library/_interpretation_qa.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/_interpretation_qa.py) (Git blob `660f5ab3aa168c026f57cb0a7d486b325f210303`).
- Current provenance: [`zotero_summarizer/services/library/qa_context.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/qa_context.py) (Git blob `f8b5dc3934b390fbac7085f07a6d4dd6a3055edb`).

## `zotero_summarizer/services/library/review_detail.py`

Older optional live-schema projection hooks are not part of the current frozen experimental contract. They remain in the original public source; do not add a second interpretation projection into ordinary review-detail.

- Review owners: #158, #187
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/services/library/review_detail.py)
- Current provenance: [`zotero_summarizer/services/library/interpretation.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/interpretation.py) (Git blob `b3aa4045bb6d9f98999cce103055e0f02dbaffd9`).
- Current provenance: [`zotero_summarizer/services/library/_interpretation_html.py`](https://github.com/Vlislavn/zotero-summarizer/blob/321e041e2a99e5557f7893ea766766789b5e8814/zotero_summarizer/services/library/_interpretation_html.py) (Git blob `e64db0fef3f2408123bf966f702d30a2a897aa83`).

## `zotero_summarizer/services/search/_evidence_pass.py`

Older injected/shadow evidence-pass implementation is replaced by the current bounded original-source acquisition, independent verification, receipt invalidation and promotion owner. Original oracle files remain in 191; old helper/API compatibility is not claimed.

- Review owners: #147, #191
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/services/search/_evidence_pass.py)
- Current provenance: [`zotero_summarizer/services/search/_question_evidence.py`](https://github.com/Vlislavn/zotero-summarizer/blob/3ee4eec51eef007365d2ee71bed25949d29447e8/zotero_summarizer/services/search/_question_evidence.py) (Git blob `4864af0c984e658ab483bbd3fa7fd1a015a66b3a`).
- Current provenance: [`zotero_summarizer/services/search/_question_policy.py`](https://github.com/Vlislavn/zotero-summarizer/blob/3ee4eec51eef007365d2ee71bed25949d29447e8/zotero_summarizer/services/search/_question_policy.py) (Git blob `ef7503f9331f0a010c097cb3b96d684e88ebecb8`).

## `zotero_summarizer/services/search/_question_adapter.py`

The adapter variant is consolidated into the current evidence owner and its configured generator/verifier routes. Do not restore a second answer/verification pipeline; original oracles remain separately reviewable.

- Review owners: #147, #191
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/services/search/_question_adapter.py)
- Current provenance: [`zotero_summarizer/services/search/_question_evidence.py`](https://github.com/Vlislavn/zotero-summarizer/blob/3ee4eec51eef007365d2ee71bed25949d29447e8/zotero_summarizer/services/search/_question_evidence.py) (Git blob `4864af0c984e658ab483bbd3fa7fd1a015a66b3a`).
- Current provenance: [`zotero_summarizer/services/search/_question_policy.py`](https://github.com/Vlislavn/zotero-summarizer/blob/3ee4eec51eef007365d2ee71bed25949d29447e8/zotero_summarizer/services/search/_question_policy.py) (Git blob `ef7503f9331f0a010c097cb3b96d684e88ebecb8`).

## `zotero_summarizer/services/search/_source_context.py`

Older supplied-context/shadow machinery is superseded by current acquired-original retrieval and exact character locators. Context-injection helper compatibility is not claimed; no duplicate context authority is shipped.

- Review owners: #147, #191
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/services/search/_source_context.py)
- Current provenance: [`zotero_summarizer/services/search/_question_evidence.py`](https://github.com/Vlislavn/zotero-summarizer/blob/3ee4eec51eef007365d2ee71bed25949d29447e8/zotero_summarizer/services/search/_question_evidence.py) (Git blob `4864af0c984e658ab483bbd3fa7fd1a015a66b3a`).
- Current provenance: [`zotero_summarizer/services/library/_paper_goal_summaries.py`](https://github.com/Vlislavn/zotero-summarizer/blob/3ee4eec51eef007365d2ee71bed25949d29447e8/zotero_summarizer/services/library/_paper_goal_summaries.py) (Git blob `6b9be4ddee6ce6776f5aa8bfe0d90d2c08d8e7fc`).

## `zotero_summarizer/services/setup/doctor_dry_run.py`

Dry-run behavior remains in the canonical Doctor _dry_run and bounded-attempt owner. The older extraction/helper API is not a missing second feature and is not restored. Original setup oracles and default-no CLI consent are retained separately.

- Review owners: #145, #185, #188
- [Original source](https://github.com/Vlislavn/zotero-summarizer/blob/34371710619b647a778ea89db4125d24874cf5ac/zotero_summarizer/services/setup/doctor_dry_run.py)
- Current provenance: [`zotero_summarizer/services/setup/doctor.py`](https://github.com/Vlislavn/zotero-summarizer/blob/47e7aec24658cca5c55566722e1e5a3ab6531a06/zotero_summarizer/services/setup/doctor.py) (Git blob `adf7cb4b23947f431e0bf28877f53aa06f0bae5e`).
- Current provenance: [`zotero_summarizer/services/setup/load_attempt.py`](https://github.com/Vlislavn/zotero-summarizer/blob/47e7aec24658cca5c55566722e1e5a3ab6531a06/zotero_summarizer/services/setup/load_attempt.py) (Git blob `6c8411f2b08719ae2a20aef82cd949dd2d022d5a`).
