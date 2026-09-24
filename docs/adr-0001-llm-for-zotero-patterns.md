# ADR 0001 — `llm-for-zotero` pattern boundary

Date: 2026-08-27. Status: accepted. Source reviewed: upstream 3.9.2/main,
AGPL-3.0-or-later. This project copies no upstream code and has no runtime dependency.

## Decisions

| Pattern | Decision | Local boundary |
|---|---|---|
| Evidence-preserving transcript compaction | Adapt | Ask-paper accepts bounded typed history. Older turns compact to extraction-versioned evidence handles while the recent conversational tail stays intact; no server transcript is required. |
| Provider capabilities | Adopt existing | `ProviderConfig` declares structured output, reasoning, context, cost and concurrency capabilities; deep review consumes them. Add vision/tools only for a measured workflow. |
| Reviewable writes | Adapt existing | Model-proposed Zotero changes use typed pending rows; explicit user actions remain backup-first and audited where semantic history matters. Universal undo is not justified. |
| Versioned workflow skills | Reject now | Typed services, schemas, prompts and evals already own policy; a second agent-policy layer would duplicate it. |
| Grounded citations | Extend existing | Q&A exposes only a verified quote or abstains and returns quote/location verification separately. Evidence handles invalidate when the extraction changes; review anchors retain explicit exact/fuzzy/approx match state. |
| Reusable context/coverage | Adapt | PDF text/index caching and evidence handles are extraction-versioned. Conversation state remains client-owned and bounded; add server session state only for a measured cross-device need. |
| Setup diagnostics | Adopt existing | Validation, reachability, operational probes and name-only secrets cover current provider requirements. Probe new capabilities when required. |
| Zotero-native plugin / MinerU | Reject | Revisit only after measured browser friction or a biomedical-corpus extraction win. |

## Gates

No AGPL implementation is imported. The multi-turn slice externalizes exact evidence
handles and preserves valid message boundaries; its regression suite covers compaction,
current-location verification, and invalidation after re-extraction. Stop
active upstream mining after two consecutive major-release reviews change neither this
table nor a tracked metric; thereafter review only major/security releases.
