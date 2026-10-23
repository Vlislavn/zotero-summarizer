# services/search — Targeted Search (the query-driven pull surface)

The library/triage domains are *push*: papers arrive from feeds and you cull them.
This domain is *pull*: you give a research topic, and it federates the open
literature + (later) your library, ranks by real relevance, and deep-reads a few —
a query-scoped research session, not a standing feed.

Intent parsing and agentic refinement wrap user queries, questions and retrieved
metadata with the shared escaped untrusted-input boundary before any LLM call.

```
topic + questions
   │  intent.parse_intent (1 LLM call; raw-query fallback if garbled — spec §7)
   ▼
SearchIntent ─ intent.build_query_plan ─> QueryPlan (per-source strings, NOT one universal query;
   │                                        each lexical source = tight "quoted phrase" + broad bag)
   ▼  federate.federate  (ThreadPoolExecutor — all channels at once; variants share a per-source quota)
 ┌─────────┬───────────┬──────────────────┬──────────┬───────────────────┬────────────┬──────────────┐
 │ arxiv   │ europepmc │ openalex lex+sem │ crossref │ semantic scholar  │ openreview │ library(TODO)│  → Candidate + Provenance
 └─────────┴───────────┴──────────────────┴──────────┴───────────────────┴────────────┴──────────────┘
   │  dedup.to_version_families  (union-find on shared ids; never title-merge; OR integrity flags)
   ▼
[Candidate]  ── rank.score_query_relevance ──> query_score  (OUR bge cross-encoder over the union;
   │                                                          source rank is candidate-gen ONLY)
   ▼  rank.rank_candidates — CONSTRAINED contract (spec §8):
   │     (relevance_bucket = ⌊query_score/EPSILON⌋) desc, quality_evidence desc, exact query_score desc
   │     → quality can only reorder WITHIN a relevance bucket; retracted → hard-sink
   ▼  pipeline._annotate_library_coverage (one ZoteroReader over the batch; tri-state, fail-open)
   │     in_library True/+key on a hit · False only on a confirmed id-miss · None when unknown
   ▼
   ▼  _relevance.attach_relevance — pool-relative band (strong/on_topic/weak) + ≤3 why chips
run_screen ──persist──> ResearchSession (status=screened)         [FAST: seconds]
   │  /screen then AUTO-claims screened→reviewing + spawns run_review (single-flight
   │  via session.claim); client polls GET to watch reviews fill in incrementally
   ▼  run_review  (auto-started; save_merge after light band + each deep read)   [SLOW: minutes]
 top-N ─ _fulltext.acquire_full_text ─> review.light_review (quality checklist; no text → uncertain, §9)
   │  rank_candidates again (quality now populated → re-rank the band)
   │  review.select_deep_set (2 highest-relevance + 1 quality + 1 exploration)
   ▼  _targeted_review.targeted_review — composes READ-ONLY library layers:
        assess_digest (focus=query)  +  summarize_for_goals (goals=[query]+questions)
        → brief + grounded query-lens + one grounded answer per question
```

The library's optional related-comment experiment also reuses `ResearchSession`,
`claim`, per-process ownership locks and atomic `save` under `Settings.search_dir`.
Its `related` field holds full private context identity, snapshot, prior attempts
and terminal result; `related_*` statuses never qualify for Search's screened
review claim. The shared session directory is owner-only (0700), including existing
directories; temporary files become 0600 before private bytes are written, and
atomic replacement retains that mode. Existing files are protected by the directory. Related sessions are omitted from the Search sidebar. No federation,
Search review or materialization is started by that flow. See
[related saved papers](../../../docs/related-saved-papers.md) for consent,
work ceilings, invalidation and unqualified semantic judgments.

## Why this shape

- **Per-source query plan, not one universal query** (`intent.build_query_plan`): a
  MeSH-style Europe PMC string, a concept-join for OpenAlex lexical, an English
  paragraph for the semantic/library channels. The plan is shown to the user *as*
  the plan (`QueryPlan.display`).
- **Wide-recall bag + one precise quoted phrase per lexical source** (`intent._tight_query`):
  the concept "bag" gives recall, but OpenAlex `search=` is citation-weighted, so a
  specific/new low-citation paper sinks under broad high-citation surveys. Each lexical
  source (OpenAlex/EuropePMC/arXiv) *also* issues ONE exact-match quoted phrase — the
  user's own leading clause — because it matches the paper's **title**, where a bag of
  LLM-paraphrased concepts does not (the parser turns "LLM-based agents" into "autonomous
  agents", so the literal span never survives to be quoted). `federate` unions the passes
  and `rank.py` never counts passes, so an extra pass can only enrich the pool. Measured
  (`tools/bench_search_recall.py`): the bag misses "A Survey on Evaluation of LLM-based
  Agents", the phrase `"evaluation of llm-based agents"` lands it #2 in OpenAlex. The tight
  pass is the *deterministic* recall lever; the OpenAlex semantic endpoint is flaky under
  load, so the quoted-phrase hits are the ones to trust.
- **Quality is measured BEFORE the deep set is chosen** (`review.light_review` runs
  over the top band, then `rank_candidates` re-runs, then `select_deep_set`). This
  is the expert review's core fix — a rigorous paper ranked 6th can be pulled into
  the deep read. The old additive `blend + quality_bonus` could not *guarantee* a
  high-relevance paper stays above a low-relevance one; the constrained contract does.
- **Version families, not one external id** (`dedup`): arXiv preprint + OpenAlex DOI
  + Europe PMC PMID are one work with a preferred version; provenance from every
  source is kept for a missed-paper analysis. Never title-merged (`dedup_doi_arxiv_only`).
  The merge also carries DERIVED state (query score, quality, review, verdict,
  relevance band, materialized key) off the member that holds it, so an agentic round
  re-fetching a bibliographically richer version can't silently drop a completed review.
- **Agentic refinement is opt-in and gates the review** (`refine`, `ZS_SEARCH_AGENTIC`):
  when on, PRF rounds refine the pool BEFORE the deep set is chosen — so review budget
  isn't spent on a first-round ranking. Off by default (an extra LLM call + federation
  per round). Transparent: each round's added/dropped concepts + new-paper count persist
  on the session (`refinements`) and render in the plan panel.
- **Targeted review reuses read-only layers, not `deep_review.start`**: no Zotero
  note write to suppress, no corpus-membership requirement, and the query is the lens.
  New session `review.brief` actions/grades use the shared reading projection with
  the existing light quality result and query summaries. High-friction reads become
  skims; absent quality cannot restore a self-grade. `review.model_digest` retains
  raw validated output and `reading_policy_flags` explains the cap. No extra model
  calls, classifier, ranking change or migration of saved sessions is introduced.
- **Library coverage is annotated, not filtered** (`_annotate_library_coverage`):
  each candidate gets `in_library` (tri-state) + `existing_zotero_key` + OpenAlex
  `cited_by_count`. It's advisory — a paper already in Zotero still ranks; the
  weekly paper-gap review (`.claude/commands/weekly-review.md`) reads `in_library`
  to flag which top results are *missing*. Fail-open: a failed/absent lookup stays
  `None` (unknown), never a false "not in library" that would re-surface a paper
  you already have.
- **OpenReview is SIGNAL-ONLY, never ranking** (`federate._openreview_channel`): a
  dual-judge eval refuted prestige-boost ranking, so a hit only resolves its
  OpenAlex id (title+year lookup, best-effort) to merge into the right version
  family and attaches `Candidate.peer_review` (tier/venue/rating) for one `why`
  chip (e.g. `ICLR'25 Oral`) in `_relevance.py`. Never touches `query_score` or
  `rank.py`. Off (`openreview_client=None`) when creds are unset or `ZS_OFFLINE=1`.
  The channel calls `search_openreview(...)` WITHOUT `with_ratings`, so production
  skips the per-paper rating fan-out (only the venue+tier chip is read); the rating
  field stays at its `None`/`0` default.
- **Zotero-optional**: candidates come from the open web; `_fulltext` recovers a
  PMC hit's machine-readable full text from Europe PMC's `fullTextXML` endpoint,
  else resolves an OA PDF by identifier (arXiv → Unpaywall(DOI) → direct url). The
  ONE Zotero write in this domain is `materialize.py` — the per-result "Add to
  library" button, an explicit user action, never triggered by ingested content. It
  validates the picker's collection key → a user-library name (unknown key → 400,
  never a junk auto-create), builds a create-item payload FROM the Candidate (a LIST
  of authors + `publication_date`, NOT the ranker's `to_scoring_dict` shape), writes
  atomically via `apply_feed_materialization` (a locked DB after retries → 503, no
  fake success). A coverage hit reuses `existing_zotero_key` instead of creating a
  duplicate; either path then stamps `materialized_zotero_key` back. The whole
  check-write-stamp runs under the session lock (`session.materialize_once`), so a
  concurrent auto-review can't lose the key AND two simultaneous Add clicks on one
  candidate write exactly once. Idempotent (re-add returns the existing key).

## Opt-in original-source question evidence (#30, experimental)

`ScreenRequest.evidence_policy` accepts `baseline` (default) or `question_evidence`.
HTTP → `run_screen` persists the explicit choice; the existing claimed review worker
runs `_question_evidence` before top-ten light review and top-four selection, then
`_question_policy` reapplies stable sufficient-answer promotion after quality ranking.
No questions, disabled budgets or unavailable prerequisites preserve baseline order.

The pass reuses `_fulltext.acquire_full_text`, Library's shared hybrid retrieval
context and faithbench's answer prompt/parser plus existing strict quote/answer
support guards. An independent configured Deep review route verifies Feed answers;
exactly located, unambiguous original quotes are required. No generated summary
supplies offsets. Up to eight chunks per question, 12 topic-eligible candidates and
24 attempted model-client invocations by default; `ZS_SEARCH_EVIDENCE_{CANDIDATES,CALLS,TOPIC_FLOOR}`
override within 0–30 / 0–120 / finite 0–1. Floor default 0.35 is provisional. Calls
are reserved in generation/verifier pairs and failures count. Identical question lines
reuse one assessment per candidate and count once for promotion. Internal provider
transport retries are outside this invocation cap. Source length uses the existing
quality-review cap; over-budget originals are unknown, not truncated, while admitted
acquired text remains reusable by ordinary review.

Per-question receipts preserve unknown/partial/sufficient/not-found-in-checked-text,
checked original ranges, exact extraction/question/input fingerprints, configured
route/protocol identity, capability breakdown and typed failures. Only sufficient
answers promote; not-found never asserts whole-document absence. Missing or partial
capability never penalizes. Sessions remain the sole store; `save_merge` preserves
concurrent explicit Adds and deletion stops work between boundaries. Family merges
carry receipts. `_question_policy.current_receipt` is the single validity authority
for display, invalidation and promotion: source/input, generator/verifier provider and
model IDs, relevant provider settings and local prompt/schema/protocol must match the
available loaded runtime configuration. `_evaluators` computes those identities without
clients, credentials, inference or requests. Absent/disabled/invalid metadata is unknown,
not permission to trust stored IDs. New evidence work requires the same current binding.

`ResearchSession.evidence_baseline_order` retains the observed candidate sequence before
promotion. Restoration seeds the existing stable constrained ranker with that sequence;
current quality/relevance keys still win, missing/new candidate IDs retain observed order,
and family/Add ownership is unchanged. Promotion refills only topic-eligible, nonretracted
slots; candidates outside that set keep their constrained baseline positions.
The corrected distinct-question/eligible-slot policy revises the evaluator protocol:
older bindings become unchecked without losing historical receipts.
Legacy receipts without recorded order/binding cannot certify current evidence or invent lost ties. No automatic rerun repairs them.
Invalidation changes only the in-memory snapshot, not files on read. Raw serialization
keeps exact historical receipts/answers through ordinary saves; only HTTP projection
suppresses stale answers. Stored extraction identity is historical, not a freshness
probe; unchanged metadata cannot detect remotely mutable weights. No private comments,
separate question store, classifier/index/worker or native writer is added.

The Search checkbox is explicit per page visit. Recommended shows server order and
promoted weak-band cards; user-selected relevance/prestige sorts remain independent.
Evidence and provenance live in native disclosures. See [usage](../../../docs/usage.md#experimental-search-question-evidence).
Human-held-out answerability and latency remain unmeasured; issue #30 remains open.

## Files

| file | role |
|------|------|
| `_models.py` | `Candidate` / `Provenance` / `SearchIntent` / `QueryPlan` / `ResearchSession` (JSON round-trip) |
| `intent.py` | topic → `SearchIntent` → per-source `QueryPlan` |
| `federate.py` | concurrent channel fan-out + quotas + provenance → `to_version_families`. Channels: arXiv, Europe PMC, OpenAlex (lex+sem), Crossref (broad metadata, `mailto` polite pool), Semantic Scholar (relevance-ranked, throttled), OpenReview (peer-review signal, SIGNAL-ONLY — see below). Lexical sources issue a tight+bag variant pair; the new leaves issue one scalar pass each |
| `dedup.py` | union-find version-family resolution |
| `rank.py` | cross-encoder `query_score` + the constrained re-rank contract |
| `_relevance.py` | pool-relative `relevance_band` (strong/on_topic/weak — cohort terciles of `query_score` + an absolute floor so an all-bad pool gets no "strong") + ≤3 `why` chips (relevance, cross-source agreement, quality grade/band, version standing). Mirrors `daily_select/_relevance`; attached in `run_screen` and re-derived after the review re-rank |
| `review.py` | `light_review` (quality tier) + `select_deep_set` |
| `_targeted_review.py` | query-lensed deep read (composes library read-only layers; sections are currently empty, so that unused parameter is not exposed) |
| `_fulltext.py` | OA full-text acquisition for a federated (non-Zotero) candidate: PMC → Europe PMC `fullTextXML` (a PMCID hit), else identifier → OA PDF |
| `pipeline.py` | `run_screen` (fast) + `run_review` (slow) + dependencies; strict offline rejects before external search |
| `materialize.py` | the one Search→Zotero write: `materialize_candidate(session_id, candidate_id, collection_key)` — resolve+validate collection (pre-lock 400), reuse a coverage hit or adapt Candidate→feed_payload, then check-write-stamp under the session lock via `session.materialize_once` (concurrent Adds write once) |
| `refine.py` | bounded opt-in agentic PRF before auto-review; no-ops under strict offline |
| `session.py` | one-JSON-per-session persistence under `settings().search_dir` + per-session lock: `claim` (status CAS, single-flights the worker), `save_merge` (whole-session save that preserves a concurrent Add's `materialized_zotero_key`), `update` (read-modify-write), `materialize_once` (Add's check-write-stamp under the lock, single-write). Malformed id → `APIError(400)` |

## Execution and identity boundaries

Screening accepts a nonblank topic of at most 4,000 characters and up to ten
nonblank questions of at most 1,000 characters each. The existing `ScreenRequest`
now lives in `_models`; HTTP, direct screening and persisted review/claim reuse
it. Surrounding whitespace is trimmed; invalid entries are rejected, not dropped.
These are interactive work ceilings, not estimates of model capacity.

Strict offline rejects screening, review claims, dependency construction and
full-text acquisition with `strict_offline` before model/source work. A saved
online session cannot bypass the rule by being reviewed after restart offline.
Reading saved results and explicitly filing a result locally remain available.

Every candidate carries a persisted `candidate_id`, included in HTTP JSON.
Unidentified observations get UUIDs, never title hashes; metadata enrichment
keeps the existing family's first address while selecting the richest metadata.
Legacy unidentified rows derive stable session/slot addresses on read, persisted
by the next ordinary save. Duplicate/invalid persisted addresses fail before
targeting or publication. Previously misattributed historical Zotero keys cannot
be inferred or repaired automatically; existing saved values are preserved.

`QueryPlan.display()` is now the server's actual wire projection: React renders
its ordered `{source, query}` rows, including repeated source variants and
OpenReview. It is recomputed from the raw plan when loaded; a saved display copy
is not authoritative. The unused taxonomy constant, scoring adapter and
`version_family_id` field are removed (old extra JSON fields remain ignored).
Search notes reuse the existing provenance marker plus escaped title/query/text;
they no longer pass a string to the structured triage-summary renderer.

Variant passes divide a lexical source's quota across its query variants, so
adding tight/bag queries does not multiply that source's candidate budget.
Ranking epsilon must be finite and positive; invalid environment values fall
back to the default and invalid direct overrides raise `ValueError`. Targeted
review pairs answers only with nonblank questions. Review failures are recorded
on the affected paper and do not stop later candidates. The session list skips
individual unreadable/corrupt files; deleting a session locks its store entry,
marks it cancelled, and prevents in-flight review saves from recreating it.

## Deferred (ponytail seams, known ceilings)

Only user-confirmed restrictions gate new searches. Model-proposed restrictions
remain visible suggestions, not filters; unknown-origin legacy plans retain their
old literal behavior until explicitly replaced. Semantic queries describe the
information need without appended filter instructions. Refinement retains domain,
optional terms and authority; dropped concepts remain advisory.

OA PDF acquisition explicitly passes the selected `Settings.pdf_cache_dir`
(`data/pdfs/`), shared with Library acquisition only within the same project.

- **Library channel**: `federate` already accepts a `LibraryFinder`; wiring it needs
  DOI/arXiv identifier columns on `corpus_embeddings` so a library hit can
  cross-source dedup against externals. Until then `default_deps` passes `None`.
- **OpenAlex quota**: the keyless polite pool has a tiny per-request budget (verified
  live: "insufficient budget"). arXiv + Europe PMC + Crossref carry a run on their own;
  OpenAlex fills in when a mailto/key is configured. OpenAlex is now wired
  INDEPENDENTLY of the prestige feature (`pipeline._search_openalex_client`) — a
  Search-dedicated keyless client is built when the app's prestige client is absent,
  so the broadest source contributes regardless of the prestige toggle.
- **bioRxiv/medRxiv**: not a dedicated channel — OpenAlex + Europe PMC both index
  bioRxiv/medRxiv preprints, and Crossref carries their DOIs, so the coverage is
  already federated. Add a dedicated leaf only if a gap shows up in a real run.

## Constraint authority

New parsed restrictions are `model_proposed`, shown as confirmation-required plan
rows, not eligibility gates. The screen request may supply `constraints` with
`must_include`, `must_not_include`, and `study_types` arrays; these are explicitly
`user_confirmed`. Exact phrase gates still apply for includes/excludes. Confirmed
types require `Candidate.publication_types`. Europe PMC's `pubTypeList.pubType`
and OpenAlex's `type` are retained and merged across identifier families. Broad
`article`/`Journal Article` metadata does not establish a review. Under an explicit
type filter, inadequate metadata is recovered by exact DOI via Europe PMC before
filtering (online only). `ZS_SEARCH_TYPE_LOOKUPS` defaults to 5, accepts 0–15, and
fails on invalid configuration. Requested-type whole-token phrases in titles
(case/space/hyphen normalized) prioritize this bounded exact-DOI lookup only;
they never establish eligibility. Both priority groups retain original order.
Adequate requested-type metadata skips lookup. Recovery accounting records
`unattempted` eligible unknowns and `confirmed` newly established requested types;
empty hits remain unknown, not evidence of HTTP failure. Remaining unknowns are disclosed, not inferred
from titles/abstracts. Existing saved plans without origin remain `legacy_unknown` and retain
literal filtering; they are not automatically migrated. Refinement drop terms
are advisory and never become exclusions. Semantic payloads state the information
need without Boolean/filter instructions.

The plan persists observed per-source accounting before constraint removal:
observations, contributing families, duplicate observations, accepted families,
constraint rejections and unknown-type rejections. A cross-source family counts
once under each contributing source, so source totals are not globally additive.
Empty channels are not evidence of provider availability or a complete search.
The plan discloses scholarly-source limits for handbooks/official guidance.

## Complementary retrieval and receipts

New plans keep one raw/tight pass and bounded domain-anchored concept groups.
Aliases and optional `related_terms` are separate from core concepts and mandatory
constraints; `domain` is a model proposal, never a claim of regulatory validity.
OpenAlex receives lexical text, Europe PMC grouped Boolean alternatives, arXiv
field-scoped terms. Each source's variants divide its existing quota; OpenAlex's
existing two-lexical-pass cap remains. No additional provider framework.

Accounting snapshots observations before mutable family merging. Every scheduled
variant records its query, allocation, observations and state, including zero hits.
Best-effort leaves do not distinguish transport failure from a genuine empty
result: those states stay `unknown`, never successful. Source family counts are
cross-source attributed (not globally additive); type-unknown and other rejection
counts survive persistence. Refinement stores its round's accounting separately.

Simplification check: reuse provider search leaves, identifier dedup, session
serialization and existing plan display; no competing retrieval/ranking loop.

Fresh semantic/library queries use the raw user topic and questions, not the model's expanded canonical restatement. The executable two-pass lexical plan covers all bounded concepts/acronyms in its broad OR pass. A model-proposed domain absent from the raw topic is disclosed, not a mandatory anchor; unspecified context asks the user to refine the existing topic. Metadata for a broad Review does not certify a Systematic Review: request-relative exact-DOI recovery runs before unconfirmed-type exclusion.
