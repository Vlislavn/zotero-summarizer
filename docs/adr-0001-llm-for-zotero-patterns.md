# ADR 0001 — `llm-for-zotero` pattern boundary

Date: 2026-08-27. Status: accepted. Source reviewed: upstream 3.9.2/main,
AGPL-3.0-or-later. This project copies no upstream code and has no runtime dependency.

## Decisions

| Pattern | Decision | Local boundary |
|---|---|---|
| Evidence-preserving transcript compaction | Defer | Ask-paper requests are stateless; add only with a server-side multi-turn transcript. |
| Provider capabilities | Adopt existing | `ProviderConfig` declares structured output, reasoning, context, cost and concurrency capabilities; deep review consumes them. Add vision/tools only for a measured workflow. |
| Reviewable writes | Adapt existing | Model-proposed Zotero changes use typed pending rows; explicit user actions remain backup-first and audited where semantic history matters. Universal undo is not justified. |
| Versioned workflow skills | Reject now | Typed services, schemas, prompts and evals already own policy; a second agent-policy layer would duplicate it. |
| Grounded citations | Adopt existing | Q&A exposes only a verified quote or abstains. It asserts no page; review anchors carry explicit exact/fuzzy/approx match state. |
| Reusable context/coverage | Defer session state | PDF text/index caching is extraction-versioned by path+mtime. Add per-session coverage only with multi-turn sessions. |
| Setup diagnostics | Adopt existing | Validation, reachability, operational probes and name-only secrets cover current provider requirements. Probe new capabilities when required. |
| Zotero-native plugin / MinerU | Reject | Revisit only after measured browser friction or a biomedical-corpus extraction win. |

## Gates

No AGPL implementation is imported. A future multi-turn slice must externalize exact
evidence handles, preserve valid message boundaries, and beat the stateless baseline on
task completion, citation precision, abstention accuracy, latency and token use. Stop
active upstream mining after two consecutive major-release reviews change neither this
table nor a tracked metric; thereafter review only major/security releases.
