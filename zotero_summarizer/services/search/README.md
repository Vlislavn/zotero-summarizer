# services/search — Targeted Search (the query-driven pull surface)

The library/triage domains are *push*: papers arrive from feeds and you cull them.
This domain is *pull*: you give a research topic, and it federates the open
literature + (later) your library, ranks by real relevance, and deep-reads a few —
a query-scoped research session, not a standing feed.

```
topic + questions
   │  intent.parse_intent (1 LLM call; raw-query fallback if garbled — spec §7)
   ▼
SearchIntent ─ intent.build_query_plan ─> QueryPlan (per-source strings, NOT one universal query)
   │
   ▼  federate.federate  (ThreadPoolExecutor — all channels at once)
 ┌────────────┬───────────────┬────────────────────┬───────────────┐
 │ arxiv      │ europepmc     │ openalex lex+sem   │ library (TODO) │   each hit → Candidate + Provenance
 └────────────┴───────────────┴────────────────────┴───────────────┘
   │  dedup.to_version_families  (union-find on shared ids; never title-merge; OR integrity flags)
   ▼
[Candidate]  ── rank.score_query_relevance ──> query_score  (OUR bge cross-encoder over the union;
   │                                                          source rank is candidate-gen ONLY)
   ▼  rank.rank_candidates — CONSTRAINED contract (spec §8):
   │     (relevance_bucket = ⌊query_score/EPSILON⌋) desc, quality_evidence desc, exact query_score desc
   │     → quality can only reorder WITHIN a relevance bucket; retracted → hard-sink
   ▼
run_screen ──persist──> ResearchSession (status=screened)         [FAST: seconds]
   │
   ▼  run_review  (on demand)                                     [SLOW: minutes]
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
- **Quality is measured BEFORE the deep set is chosen** (`review.light_review` runs
  over the top band, then `rank_candidates` re-runs, then `select_deep_set`). This
  is the expert review's core fix — a rigorous paper ranked 6th can be pulled into
  the deep read. The old additive `blend + quality_bonus` could not *guarantee* a
  high-relevance paper stays above a low-relevance one; the constrained contract does.
- **Version families, not one external id** (`dedup`): arXiv preprint + OpenAlex DOI
  + Europe PMC PMID are one work with a preferred version; provenance from every
  source is kept for a missed-paper analysis. Never title-merged (`dedup_doi_arxiv_only`).
- **Targeted review reuses read-only layers, not `deep_review.start`**: no Zotero
  note write to suppress, no corpus-membership requirement, and the query is the lens.
- **Zotero-optional**: candidates come from the open web; `_fulltext` recovers a
  PMC hit's machine-readable full text from Europe PMC's `fullTextXML` endpoint,
  else resolves an OA PDF by identifier (arXiv → Unpaywall(DOI) → direct url). A
  push to your Inbox (materialize) stays an explicit user action — this domain
  never writes to Zotero.

## Files

| file | role |
|------|------|
| `_models.py` | `Candidate` / `Provenance` / `SearchIntent` / `QueryPlan` / `ResearchSession` (JSON round-trip) |
| `intent.py` | topic → `SearchIntent` → per-source `QueryPlan` |
| `federate.py` | concurrent channel fan-out + quotas + provenance → `to_version_families` |
| `dedup.py` | union-find version-family resolution |
| `rank.py` | cross-encoder `query_score` + the constrained re-rank contract |
| `review.py` | `light_review` (quality tier) + `select_deep_set` |
| `_targeted_review.py` | query-lensed deep read (composes library read-only layers) |
| `_fulltext.py` | OA full-text acquisition for a federated (non-Zotero) candidate: PMC → Europe PMC `fullTextXML` (a PMCID hit), else identifier → OA PDF |
| `pipeline.py` | `run_screen` (fast) + `run_review` (slow) + `SearchDeps`/`default_deps` |
| `session.py` | one-JSON-per-session persistence under `settings().search_dir` |

## Deferred (ponytail seams, known ceilings)

- **Library channel**: `federate` already accepts a `LibraryFinder`; wiring it needs
  DOI/arXiv identifier columns on `corpus_embeddings` so a library hit can
  cross-source dedup against externals. Until then `default_deps` passes `None`.
- **OpenAlex quota**: the keyless polite pool has a tiny per-request budget (verified
  live: "insufficient budget"). arXiv + Europe PMC carry a run on their own; OpenAlex
  fills in when a mailto/key is configured.
