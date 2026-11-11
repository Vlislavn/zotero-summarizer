# services/search — Targeted Search (query-driven pull)

Contract for changing this package. Behavior as users see it:
[docs/features.md](../../../docs/features.md) (search, [question evidence](../../../docs/features.md#search-question-evidence),
[discovery](../../../docs/features.md#saved-paper-discovery),
[related saved papers](../../../docs/features.md#related-saved-papers)).

```
topic + questions
  │ intent.parse_intent (1 LLM call; raw-query fallback) → build_query_plan → QueryPlan (per-source strings)
  ▼ federate.federate (thread pool, all channels at once; variants share a per-source quota)
  arxiv · europepmc · openalex lex+sem · crossref · semantic scholar · openreview · library (unwired)
  │ dedup.to_version_families (union-find on shared ids; never title-merge)
  ▼ rank.score_query_relevance (our cross-encoder over the union; source rank is candidate-gen only)
  ▼ rank.rank_candidates (constrained contract) → pipeline._annotate_library_coverage → _relevance.attach_relevance
run_screen ─save→ ResearchSession(status=screened)                                   [fast]
  │ /screen claims screened→reviewing (session.claim) and spawns run_review          [slow]
  ▼ run_review: _fulltext → review.light_review → re-rank → review.select_deep_set
    → _targeted_review (read-only library layers: assess_digest + summarize_for_goals)
```

## Files

| file | responsibility |
|---|---|
| `__init__.py` | `require_online()`: strict offline → `APIError strict_offline` (409) |
| `_models.py` | `ScreenRequest`, `ConfirmedConstraints`, `Provenance`, `Candidate`, `SearchIntent`, `QueryPlan`, `ResearchSession` (JSON round-trip, `to_dict(for_storage=)`) |
| `intent.py` | topic → `SearchIntent` → per-source `QueryPlan` (`_tight_query` quoted phrase + broad bag; `QueryPlan.display()` is the wire projection (plain source labels, e.g. «OpenAlex · keyword search», «Must mention (title/abstract)», «Filters» = AI-suggested vs confirmed-by-you, «Needs your confirmation: …»; no row for `legacy_unknown`); its `retrieval <source>` rows are plain sentences from `_accounting.describe_retrieval`, plus one «Some sources failed» row when a receipt says failed) |
| `_queries.py` · `_publication_types.py` · `_accounting.py` | complementary lexical passes · exact-DOI publication-type recovery (`ZS_SEARCH_TYPE_LOOKUPS`) · per-source retrieval accounting |
| `federate.py` | channel fan-out, quotas, provenance, constraint filter, OpenReview signal channel |
| `dedup.py` | version families; `reconcile_operation_state` |
| `rank.py` | `score_query_relevance` + `rank_candidates` (`ZS_SEARCH_RANK_EPSILON`) |
| `_relevance.py` | pool-relative band (strong/on_topic/weak; `ZS_SEARCH_STRONG_FLOOR`/`ZS_SEARCH_MILD_FLOOR`) + ≤3 `why` chips |
| `review.py` | `light_review` (quality tier), `select_deep_set` |
| `_targeted_review.py` | query-lensed brief + one grounded answer per nonblank question |
| `_fulltext.py` | OA full text for non-Zotero candidates: PMC → Europe PMC `fullTextXML`, else arXiv/Unpaywall/direct OA PDF; optional request-local `outcome` |
| `_question_evidence.py` · `_question_policy.py` | opt-in original-text question evidence · receipt validity (`current_receipt`) and promotion |
| `refine.py` | opt-in agentic PRF rounds before deep-set selection (`ZS_SEARCH_AGENTIC`) |
| `pipeline.py` | `SearchDeps`/`default_deps`, `run_screen`, `run_review`, `_annotate_library_coverage` |
| `materialize.py` | the only Search→Zotero write: `materialize_candidate` |
| `discovery.py` | saved-seed discovery: public retrieval + optional consent-bound assessment |
| `session.py` | one JSON per session under `Settings.search_dir`: `save`, `save_merge`, `update`, `claim`, `materialize_once`, `find_discovery`, `list_sessions`, `delete`/`is_deleted` |

## Ranking and review invariants

- Sort key (`rank.constrained_key`): `(⌊query_score/ε⌋, quality_evidence, query_score + goal tiebreak)` descending.
  Quality reorders only within a relevance bucket; retracted candidates sink. ε is finite and positive; an
  invalid environment value falls back to the default, an invalid direct override raises `ValueError`.
- Quality is measured before the deep set: `light_review` over the top band → `rank_candidates` again →
  `select_deep_set` (2 highest-relevance, 1 quality, 1 exploration). Missing text yields `uncertain`.
  A review failure is recorded on that paper and later candidates continue. Papers are light-/deep-reviewed
  `SearchDeps.paper_concurrency` at a time (`pipeline.MAX_PAPER_CONCURRENCY` = 4, bounded by each review stage's provider cap; a local model
  stays serial); `screened_count` rises as each light review lands ("reviewed N of 10") and each deep review is saved as it lands. The deep-review
  pool is not involved: Search calls the stage clients directly.
- `rank.py` never counts passes or sources; extra passes only enrich the pool.
- Lexical sources (OpenAlex, Europe PMC, arXiv) issue a tight quoted-phrase pass plus a bag pass;
  variants divide the source's quota, so variants never multiply the candidate budget. OpenAlex lexical is
  capped at two passes. Semantic/library queries use the raw topic and questions, not the model's restatement.
- OpenReview is signal-only: it resolves an OpenAlex id for family merging and sets `Candidate.peer_review`
  for one chip. It never touches `query_score` or `rank.py`, calls `search_openreview` without
  `with_ratings`, and is off when `openreview_client is None` (no credentials or `ZS_OFFLINE=1`).
- Agentic refinement (`refine`) runs before the deep set; per-round concepts/counts persist in `refinements`;
  dropped terms are advisory, never exclusions. It no-ops under strict offline.
- Targeted review composes read-only layers (no `deep_review.start`, no Zotero note write). Answers pair only
  with nonblank questions. `review.brief` actions/grades use the shared reading projection over the light
  quality result; absent quality never restores a self-grade; `reading_policy_flags` explains caps.
- `score_query_relevance(reranker_enabled=False)` scores lexically without loading the cross-encoder; Discovery
  passes `config.corpus.reranker_enabled` (`ZS_CORPUS_RERANKER_ENABLED`, see [overrides](../../../docs/overrides.md)).

## Identity and dedup

- Families are union-find over every normalized DOI/arXiv/PMID/alias key (`Candidate.identifier_keys()`;
  `family_identifier_keys` retains the full union and survives JSON). Titles never merge. Merging carries
  derived state (query score, quality, review, verdict, band, materialized/planned key, evidence receipts)
  off whichever member holds it.
- Coverage (`_annotate_library_coverage`) is advisory and tri-state: `in_library` True + key on any actual
  identifier hit; False only when every retained DOI/arXiv alternative is a confirmed miss; `None` when
  unknown. Arxiv values containing SQL-LIKE wildcards are withheld from the reader. Merges keep unknown
  members unknown and let a genuine positive key dominate stale misses. Fail-open: never a false
  "not in library".
- `candidate_id` is persisted and in HTTP JSON; unidentified observations get UUIDs, never title hashes.
  Legacy rows derive stable session/slot ids on read. Duplicate/invalid persisted ids fail before targeting.
- Request bounds (`ScreenRequest`): topic 1–4,000 chars, ≤10 questions of 1–1,000 chars, whitespace trimmed,
  invalid entries rejected (not dropped), UTF-8-encodable. `claim` revalidates them.
- Publication types come from Europe PMC `pubTypeList.pubType` and OpenAlex `type` only, never from prose.
  Under an explicit type filter, inadequate metadata is recovered by exact DOI (online only; budget
  `ZS_SEARCH_TYPE_LOOKUPS` default 5, 0–15, invalid → error). Remaining unknowns are disclosed, not inferred.

## Constraint authority and accounting

- `SearchIntent.constraint_origin` is `legacy_unknown` (literal filtering retained), `model_proposed`
  (shown as pending plan rows, not gates) or `user_confirmed` (`ScreenRequest.constraints`:
  `must_include`, `must_not_include`, `study_types`; exact phrase gates apply).
- `QueryPlan.retrieval_accounting` records per-source observations, contributing/accepted families, duplicate
  observations, constraint and unknown-type rejections, and each variant's query/allocation/state (zero hits
  included), snapshotted before family merging. Counts are cross-source attributed, not additive. Empty
  channels are not evidence of availability or completeness.
- `federate(observe_outcomes=True)` keeps one request-local outcome dict per scheduled arXiv, Crossref,
  Europe PMC, Semantic Scholar and OpenAlex (lex/sem) request; ordinary calls pass no receipt keyword and a
  silent adapter stays `unknown`. OpenReview/library are uninstrumented.

## Persistence (`session.py`)

- Directory is 0700 (also re-chmodded when it exists); the temp file is chmod 0600 before bytes are written;
  `save` is temp + atomic replace under a process-wide guard. A malformed id → `APIError(400)`.
- `claim` is the status compare-and-set that single-flights the review worker. `save_merge` saves a whole
  session while preserving concurrent Add keys; `update` is read-modify-write; locks are per-process,
  non-reentrant — never call `update`/`save_merge`/`claim` from a `materialize_once` writer.
- `delete` unlinks the file and records the id; `save`/`save_merge` then refuse to recreate it and workers stop
  at their next boundary. `list_sessions` skips unreadable files and omits `related` sessions.
- Strict offline rejects screening, review claims, `default_deps` and full-text acquisition before any
  source/model work; reading saved sessions and local Add stay available.
- Prompts: intent parsing, refinement and retrieved metadata pass through the shared escaped untrusted-input
  boundary. `default_deps` resolves clients through `services.llm.factory` with the `RuntimeState` cache and
  consent fences; the Search OpenAlex client takes its cache from `RuntimeState`, independent of prestige.
- Related-paper sessions reuse `ResearchSession`, `claim`, locks and atomic `save`; `related` status never
  qualifies for Search's review claim. Default/wire serialization exposes `related` as `{}`; only
  `for_storage=True` retains it. That flow starts no federation, review or materialization.
  Ordinary HTTP projections omit an empty Discovery key; storage keeps `{}`.

## Explicit Add (`materialize.py`)

- The only Zotero write in this package; an explicit user action, never triggered by ingested content.
  Collection key → user-library name is validated first (unknown → 400, never a junk collection).
- The payload is built from the `Candidate` (list of authors + `publication_date`, not `to_scoring_dict`).
  The write is `ZoteroWriter.apply_feed_materialization` (locked DB → 503; no fake success).
- Check-write-stamp runs under the session lock via `session.materialize_once`, so concurrent Adds write once.
  An existing coverage hit reuses `existing_zotero_key` (no writer, no plan); either path stamps
  `materialized_zotero_key`. Re-add returns `already_added`.
- A new item first saves `planned_zotero_key` (eight ASCII uppercase letters/digits) in the session before
  backup/native work; a failed prewrite save dispatches nothing. Native or finalization failure retains the
  plan and the retry reuses the writer's existing-key no-op. A plan is not coverage or a success receipt.
- Confirmed coverage that disagrees with a retained plan → 409 `search_write_conflict` before any write or
  stamp. Stale discovery plans use `existing_only=True` (exact key, empty steps, no backup, else 409).
  Add preflights discovery currency before effects.
- Family merges, enrichment and stale background saves preserve planned/materialized keys; conflicting
  selected-family identities fail before any update. Not claimed: distributed exactly-once, cross-library
  replay, repair of later user edits, or one transaction spanning session JSON and native SQLite.

## Question evidence (opt-in)

- `ScreenRequest.evidence_policy` is `baseline` (default) or `question_evidence`. The existing review worker
  runs `_question_evidence` before top-ten light review; `_question_policy.apply_question_policy` promotes
  sufficient answers after quality ranking. No questions, disabled budgets or missing prerequisites keep
  baseline order.
- Reuses `_fulltext.acquire_full_text`, Library hybrid retrieval, faithbench `ANSWER_PROMPT`/`parse_answer` and
  strict quote guards, plus an independent configured Deep-review verifier. Only exactly located, unambiguous
  original quotes count; generated summaries never supply offsets. Over-budget originals are unknown, not
  truncated.
- Limits: 8 chunks/question, 12 candidates, 24 attempted model invocations (generation/verifier pairs; failures
  count), floor 0.35; `ZS_SEARCH_EVIDENCE_{CANDIDATES,CALLS,TOPIC_FLOOR}` override within 0–30 / 0–120 / finite 0–1.
  Identical question lines share one assessment per candidate.
- `_question_policy.current_receipt` is the single validity authority for display, invalidation and promotion:
  source/input fingerprints, generator/verifier provider+model IDs, provider settings and prompt/schema/protocol
  (`_EVIDENCE_PROTOCOL`) must match the loaded runtime configuration (`_evaluators` builds identities without
  clients, credentials or requests). Absent/invalid metadata is unknown, not trust in stored IDs. Malformed
  retained hashes, bounds, passages or spans invalidate the receipt (baseline order restored), never become
  a checked negative.
- Receipts keep unknown/partial/sufficient/not-found-in-checked-text; only sufficient promotes, not-found never
  asserts whole-document absence, and missing capability never penalizes. Invalidation changes only the
  in-memory view: raw storage keeps historical receipts, HTTP projection suppresses stale ones.
- `ResearchSession.evidence_baseline_order` retains pre-promotion order; restoration seeds the stable
  constrained ranker with it. Promotion fills only topic-eligible, nonretracted slots. Legacy receipts without
  order/binding cannot certify current evidence; nothing reruns automatically. `save_merge` keeps concurrent
  Adds; deletion stops work between boundaries.

## Discovery (`discovery.py`)

- Retrieval sends only public seed metadata (title, abstract, identifiers; bounded to the Search request ceiling)
  and schedules only OpenAlex lexical (≤2 variants) + semantic; ordinary Search stays all-source. The seed's
  private comments reach a model only through the consent-bound assessment (`consent_required` 409 on a
  changed model boundary).
- A seed need not be saved: feed-only papers search from public metadata too (`admission.state` stays informational). Candidates sharing the seed's identifier or normalized title (the seed itself) are dropped.
- Saved-family identifiers are excluded; app-known identifiers still exclude when no native reader exists, but
  remaining membership stays unknown (`in_library=None`) and is never a default new-paper recommendation.
- Whole-library membership (kept app decisions + every Zotero item/identifier, identity families, digest) is one
  process-wide entry in `discovery._membership`, keyed by the size/mtime of `triage_history.db` and `zotero.sqlite`
  (+ `-wal`); any write (Add, sync, label) changes the key and the next call rebuilds it. Requests copy the shared
  family map and add their seed; nothing seed-specific is cached.
- Raw session snapshots serialize only for storage. `discovery_view` is the HTTP projection: while
  pending/running/failed, stale or interrupted it hides seed comments, assessment items/IDs and
  recommendations; public retained metadata stays visible. The context fingerprint hashes content only (seed
  context, content digest of kept/native membership, model boundary, config, source files) — never file
  size/mtime markers such as `corpus_cache.db` (also the OpenAlex cache, written constantly by Today preparation
  and by Find itself), which made unchanged seeds 409 `discovery_stale` (#275). Freshness fingerprints (`_freshness`) must be
  `current` before work, publication and Add.
- Only explicit Review may claim a discovery session (`explicit_review_required` 409).

## Extension points and deferred seams

- New source: add a `_<name>_channel` in `federate.py` plus a `QueryPlan` field in `intent`; every hit becomes a
  `Candidate` with `Provenance`. Parsers/HTTP stay in `integrations/`.
- Library channel: `federate` accepts a `LibraryFinder`, but `default_deps` passes `None` until
  `corpus_embeddings` carries DOI/arXiv columns for cross-source dedup.
- No dedicated bioRxiv/medRxiv leaf (OpenAlex, Europe PMC and Crossref index them).
- Reuse provider leaves, identifier dedup, session serialization and `QueryPlan.display()`; add no
  competing retrieval/ranking loop.
