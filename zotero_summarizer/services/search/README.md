# services/search — Targeted Search (the query-driven pull surface)

The library/triage domains are *push*: papers arrive from feeds and you cull them.
This domain is *pull*: you give a research topic, and it federates the open
literature + (later) your library, ranks by real relevance, and deep-reads a few —
a query-scoped research session, not a standing feed.

```
topic + questions
   │  intent.parse_intent (1 LLM call; raw-query fallback if garbled — spec §7)
   ▼
SearchIntent ─ intent.build_query_plan ─> QueryPlan (per-source strings, NOT one universal query;
   │                                        each lexical source = tight "quoted phrase" + broad bag)
   ▼  federate.federate  (ThreadPoolExecutor — all channels at once; one pass per variant, unioned)
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
  fake success), then stamps `materialized_zotero_key` back. The whole
  check-write-stamp runs under the session lock (`session.materialize_once`), so a
  concurrent auto-review can't lose the key AND two simultaneous Add clicks on one
  candidate write exactly once. Idempotent (re-add returns the existing key).

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
| `materialize.py` | the one Search→Zotero write: `materialize_candidate(session_id, candidate_id, collection_key)` — resolve+validate collection (pre-lock 400), Candidate→feed_payload adapter, then check-write-stamp under the session lock via `session.materialize_once` (concurrent Adds write once) |
| `refine.py` | bounded opt-in agentic PRF before auto-review; no-ops under strict offline |
| `session.py` | one-JSON-per-session persistence under `settings().search_dir` + per-session lock: `claim` (status CAS, single-flights the worker), `save_merge` (whole-session save that preserves a concurrent Add's `materialized_zotero_key`), `update` (read-modify-write), `materialize_once` (Add's check-write-stamp under the lock, single-write). Malformed id → `APIError(400)` |

## Deferred (ponytail seams, known ceilings)

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
