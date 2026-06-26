<!-- Synthesized by a 13-agent SOTA-research + design + adversarial-judge workflow (reranker path corrected to services/model/reranker.py; prompts.map/reduce confirmed currently unwired). -->

# Goal-Aligned Glassbox Brief — Definitive Design

Synthesis of 3 proposals. Backbone = **Design 3 (Glassbox)** for its quality-eval fidelity and clean LOC/layering posture (winner in 2 of 3 verdicts), with grafts the judges flagged: **Design 1**'s always-render 6-cell Goal Match Board, structural pre-filter, MISS-cell-as-distinct-claim guard, versioned CLAIM_FIELDS, and prestige floor; **GoalLens (#2)**'s reference-exemplar anchoring, reranker degradation ladder, section-count degradation contract, and independently-skippable layers. Every infra claim below was verified against the working tree.

---

# 1. Current brief vs. the goal — the concrete gaps

- **Relevance is a single flat string, not per-goal.** `zotero_summarizer/services/library/_paper_read_html.py::_digest_section_html` (line 201) renders goal-fit as one `_row("Relevance", "relevance")` inside a collapsible `<details>`. There is no per-goal breakdown and nothing above the fold — the reader cannot see which of their 6 goals a paper touches at a glance. The 6 `research_goals` enter only as a semicolon-joined string into the `relevance` field of `quality_review._DEFAULT_DIGEST_PROMPT`.

- **Only 5 of the 11 desired `summary_structure` parts are wired.** `models/triage.py::PaperDigest` (extends `QualityReview`, line 87) has `tldr/read_decision/read_why/read_parts/relevance/controversies/impact/unknown_unknowns/implementation` + 5 scores. It has **no** `executive_summary`, `key_findings`, `methods`, or `limitations` fields. `RefinedSummary` (line 110) already carries all 11 fields with validators (`bool→Yes/No`, list-coercion) **but is unused in the digest/render path** — it's only consumed by `services/triage/summarization.py`.

- **`read_decision` is quality-only and 3-way, not goal-aware binary.** It's `read|skim|skip` derived from paper merit alone; there is no "Should I Deep Read This?" Yes/No conditioned on whether any goal fired. No goal-weighted routing exists by design (`assess_digest` does "no separate relevance re-score").

- **No reference-free rigor signal.** `assess_digest` produces 5 Likert dimensions + grade, but nothing extracts/grades the user's `triage_criteria` (external validation, ablation, CIs, dataset provenance, open-dataset access) as structured checks. Faithbench (`services/faithbench/_judge.py::judge_claim`) can ground claims that are *present* but never systematically probes for *missing* rigor. `CLAIM_FIELDS` (`_build_claims.py` line 29) is exactly 6 fields today.

- **Retrieval operates at item-level, never intra-paper.** Hybrid search (`_search.py::hybrid_search`) ranks whole library items; `corpus_read.query_affinity_for_items` reads **cached SQLite embeddings by item_id** and cannot embed ad-hoc chunks. The per-paper index `faithbench/_corpus.py::PaperChunkIndex` is **BM25-lexical only** (the deliberate v1 simplification). There is no per-chunk dense leg and no goal-conditioned chunk retrieval — so "key sections to read *for me*" cannot be produced today.

---

# 2. SOTA grounding — what we adopt + WHY

| Practice | Why (source) | Where it lands |
|---|---|---|
| **ARR Soundness-vs-Excitement split** — never one blended number | ACL Rolling Review encodes the hard-won lesson that "sound" and "relevant-to-me" are orthogonal; conflating them produces bad triage | Two top-level chips: **Rigor** (author-blind band) + **Relevance** (reader-specific) |
| **Keshav "first pass" + five C's** | Canonical fast-triage model; "highlights in 5 min or the paper is never read" is the brief's job spec | Hero strip: Category tag + 1-line TL;DR + Skip/Skim/Deep-read verdict |
| **Extract-then-abstract, query-focused per goal** (not map-reduce ×6) | DETQUS 2025 + aspect-oriented QFS: pre-reducing input to query-relevant passages raises faithfulness and runs on small open models (Qwen2.5/3); whole-paper map-reduce degrades on mid-context detail and is 6× the tokens | Per-goal retrieval over the paper's own chunks; map-reduce reserved for the ONE neutral exec summary |
| **ACLSum / FacetSum facet decomposition** (challenge/approach/outcome) | Expert-annotated SOTA: the unit of a scholarly summary is the `(aspect → short grounded summary)` pair; narrow single-facet questions beat free-summarize | `prompts.goal_facet` sub-structure inside each fired goal |
| **Hybrid BM25+dense → RRF → cross-encoder rerank** | Two-stage hybrid beats every single-stage method; we already implement it (`_search._rrf` k=60 + `get_reranker`) | Reused verbatim, retrieval **unit** changed to intra-paper chunks |
| **Section-aware chunking with section metadata** | Structure-aware splits beat sliding windows on retrieval + cost; section-title metadata is the mechanism for citation-back and "key sections" | Chunks carry `section_title + page + char-span`; "Key Sections" = distinct section titles of the grounded quotes |
| **Relevance GATE before summarizing** ("Selection, not Salience", 2025) | Personalization helps *selection/retrieval*, not generative prose style; forced 6-goal coverage inflates false positives | Per-goal floor; sub-floor goals emit explicit "not addressed" |
| **Grounding + abstention** (qa.py honest modes; MiniCheck/SummaC) | Quote-grounded outputs are the strongest verifiable baseline; rewarding "not attempted" over wrong keeps triage honest | Reuse `qa._quote_is_grounded` (≥6 words/≥40 chars, verbatim substring) per quote |
| **Decomposed yes/no/NA rubric over a fine score** (CheckEval, G-Eval, ReviewerGPT) | Binary/low-cardinality = highest inter-rater reliability; LLMs can't calibrate fine scales; GPT-4 is good at targeted checks, bad at holistic ranking | 3-band FLAG/NEUTRAL/HIGHLIGHT from a weighted checklist, **never** a 1-100 score |
| **Self-consistency + confidence discounting** (overconfidence, arxiv 2508.06225) | Judges are systematically overconfident; stable-across-runs = trustworthy | Quality band run ~3×; commit only on agreement, else "uncertain — human look" |
| **Reference-exemplar anchoring** (graft from #2) | Pairwise agrees with humans better but is O(N²) and position-biased; embedding one known-good + one known-weak exemplar makes a pointwise judgment quasi-relative without N² cost — best for a single offline model | One good + one weak exemplar per Keshav category in `goals.yaml` |
| **Bias guards** (Justice or Prejudice 2410.02736; self-preference 2410.21819) | Prestige halo, verbosity, rubric position bias are measured failure modes | Author-blind scorer, length-neutral instruction, permuted rubric-option order ×2, `<untrusted_input>` injection sanitization |
| **RIGOURATE intra-paper overstatement** (arxiv 2601.04350) | The marquee citation-free quality signal: abstract claims vs body evidence | Extract abstract's 2-4 headline claims, retrieve supporting passages via the same per-paper index, flag scope/strength/causal mismatches |
| **TRIPOD+AI / CLAIM / data-leakage red flags** | Domain rubrics for the reader's clinical/bio goals; leakage corrupted 294+ papers — highest-yield soundness check | Conditional clinical/bio block (goals 1/3/6) + agentic block (goals 2/4/5) |

---

# 3. Goal-aligned brief structure — at-a-glance order

Rendered top-to-bottom by a new `_brief_section_html()` (in a **sibling** `_paper_read_brief.py`, graft from #2, to keep `_paper_read_html.py` under 500 LOC — it's at 404 today), inside the existing `_render_presentation` hero+sections shell. SS# = `summary_structure` index; G1-6 = goals.

### ABOVE THE FOLD (the <10s glance — fixed height, never collapsed)

1. **HERO STRIP** — title + author-blinded note + **Category** tag (method/system/benchmark/analysis/theory/survey) + **1-line TL;DR**. *(SS#1 condensed; SS#9 headline.)*
2. **TWO SPINE CHIPS** (large, side by side): **(a) RIGOR** = quality band {FLAG/NEUTRAL/HIGHLIGHT} + grade A-D + confidence/agreement dot (author-blind); **(b) RELEVANCE** = which goals fired + max fused 0-3 → must/should/could/skip (reader-specific). *The ARR split — never blended.*
3. **READ-VERDICT badge** — Deep-read | Skim | Skip + one-clause reason (read if any goal fires **AND** band ≠ FLAG). *(SS#2, upgraded to binary-with-reason.)*
4. **GOAL MATCH BOARD** (graft from #1) — fixed 6-cell grid, **all 6 goals always shown** in order G1..G6. Each cell: goal short-label + state chip + 0-3 bar + 1-clause why. **Three cell states** (the MISS-cell guard, graft from #1): `HIT` (gate cleared, evidence found), `MISS` (gate ran, no chunk cleared floor → "not addressed", a *grounded negative claim*), `NOT RETRIEVED` (neutral grey — `rank_bm25` absent / reranker still loading → degraded retrieval, **never** rendered as confident MISS). *(SS#4, structured per-goal.)*

### BELOW THE FOLD (collapsible `<details>`, lazy, ordered to match SS)

5. **Executive Summary** — 3-5 sentences from the neutral map-reduce digest. *(SS#1 full.)*
6. **Key Sections to Read** — distinct section titles of the top grounded chunks per fired goal, linking to the in-page Sections block: "for G1: §Methods, Table 4". *(SS#3.)*
7. **Per-Goal Briefs** — for each FIRED goal: ≤3-sentence facet summary + supporting quotes + key sections. *(SS#4 expanded.)*
8. **Controversial Points** — enumerated debate axes. *(SS#5.)*
9. **Industry & Academy Impact** — two labelled lines. *(SS#6.)*
10. **Key Findings** — 3-7 numbered, each with a metric + grounding quote. *(SS#9.)*
11. **Methods** — dataset provenance / preprocessing / architecture / training (the 4 sub-fields `triage_criteria` demand). *(SS#10.)*
12. **Limitations + Unknown Unknowns** — stated limits + red-flag results + surprises. *(SS#11 + SS#7.)*
13. **Implementation Quickstart** — libraries / frameworks / steps / gotchas. *(SS#8.)*
14. **QUALITY PANEL (glassbox)** — decomposed rubric grid (yes/no/NA chips), red flags, overstatements, claim-grounding pass rate; each chip expands to its quoted evidence.
15. **Conditional Rigor Block** — clinical/bio (G1/G3/G6) or agentic (G2/G4/G5) checks.
16. Existing Figures + Sections + Quick Reference (unchanged).

### Concrete example layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ [BENCHMARK]  "AgentClinic: a multimodal agent benchmark for clinics"   │
│ TL;DR: 24-agent harness on 107 simulated patient cases; GPT-4 hits     │
│ 52% diagnostic accuracy, degrades 18pts under missing-history bias.    │
│                                                                        │
│  ┌── RIGOR ─────────────┐   ┌── RELEVANCE ──────────────────────────┐  │
│  │ NEUTRAL · Grade B    │   │ MUST READ · 2 goals fired · 2.7/3     │  │
│  │ confidence ● agree ● │   │ G1 multiagent-clinical, G3 multimodal │  │
│  └──────────────────────┘   └───────────────────────────────────────┘  │
│                                                                        │
│  VERDICT:  ▶ DEEP-READ — open-source clinical agent benchmark, your   │
│            top goal; external validation present, no leakage flag.    │
│                                                                        │
│  GOAL MATCH BOARD                                                      │
│  ┌─────────────────┬─────────────────┬─────────────────┐             │
│  │ G1 Multiagent   │ G2 Autonomy/    │ G3 Multimodal   │             │
│  │ clinical  ●HIT  │ glassbox ○MISS  │ clinics   ●HIT  │             │
│  │ ███ 3/3         │ ░░░ not addr.   │ ██░ 2/3         │             │
│  │ "24-role triage │ "no determinism │ "image+text     │             │
│  │  agent society" │  discussion"    │  patient cases" │             │
│  ├─────────────────┼─────────────────┼─────────────────┤             │
│  │ G4 Autonomous   │ G5 Policy       │ G6 Agentic      │             │
│  │ SWE      ○MISS  │ enforce  ⚠N/RET │ bioinfo  ○MISS  │             │
│  │ ░░░ not addr.   │ ░░░ (reranker   │ ░░░ not addr.   │             │
│  │                 │  loading)       │                 │             │
│  └─────────────────┴─────────────────┴─────────────────┘             │
│  ▼ Executive Summary  ▼ Key Sections  ▼ Per-Goal Briefs  ▼ Quality    │
└──────────────────────────────────────────────────────────────────────┘
```

The board + 2 chips + verdict fit one viewport — the entire "what is this + which of my 6 goals does it touch + is it sound" answer. The `⚠N/RET` state for G5 is the load-bearing guard: a confident "not addressed" is itself a claim, so degraded retrieval renders neutral, never as a false negative.

---

# 4. Goal-conditioned retrieval summaries

**Architecture: extract-then-abstract, query-focused retrieval per goal.** Map-reduce (`goals.yaml prompts.map/reduce`, currently unwired) is reserved for the ONE neutral `executive_summary`. Each fired goal gets a cheap retrieval pass over the paper's **own** chunks. All on local Qwen3-35B via the `deep_review` stage client.

### Pipeline per paper (new module `services/library/_paper_goal_summaries.py`, ≤200 LOC)

**(0) Precompute goal embeddings once.** The 6 `research_goals` are standing; embed each once via `storage/corpus.py::EmbeddingCache._embed` (resident sentence-transformers model, no new dependency) and cache the 6 vectors in-process. `upsert_goals` already stores them in the corpus DB for library ranking.

**(1) Section-aware chunking.** Consume the already-extracted `content["render_sections"]` (list of `{id, title, page, text}` from `paper_render.build_paper_read_for_pdf` line 280; PDF-only papers fall back to `"Page N"` titles per `_paper_read_pdf.py` line 283). Reuse `faithbench/_corpus.py::chunk_text` (CHUNK_CHARS=1200, overlap=200) **per section** so no chunk spans two sections; tag each chunk with `section_title + page + char_span`. **Reuse the existing per-paper index cache** `qa.py::_paper_context_source` / `_TEXT_CACHE` (graft — Design 3's distinguishing move) so we never extract or build a second index.

**(2) Per-goal hybrid retrieval over intra-paper chunks** — the retrieval *unit* changes from item→chunk; no new fusion/rerank code:
- **BM25 leg**: `PaperChunkIndex.top_chunks(goal_text, k)` (already cached for the paper).
- **Dense leg** (NET-NEW in-memory code — *not* `_affinity_to_targets`, which reads cached SQLite embeddings by item_id and **cannot** embed ad-hoc chunks): a small `PaperChunkDenseIndex` that embeds each chunk **once** via `EmbeddingCache._embed`, L2-normalizes, and cosines to the 6 precomputed goal vectors. Cache the matrix per `(pdf_path, mtime)` alongside `_TEXT_CACHE` to keep RAM flat.
- **Fuse** with the existing `_search._rrf` (k=60), verbatim.
- **Rerank** the fused top-N with `services/model/reranker.py::get_reranker(...).rerank(goal_text, pairs, top_n)`.
- **Degradation ladder** (graft from #2, matches `_search.py`'s existing philosophy): `rerank → RRF-fusion-only → dense-only`. If retrieval degrades (rank_bm25 absent / reranker loading), the goal cell renders `NOT RETRIEVED`, never a confident MISS.

**(3) Relevance gate.** If the best fused/reranked chunk score for a goal is below a floor → goal is **MISS**, emit "this paper does not address `<goal>`", do **not** summarize. Only fired goals get a generated brief. This produces the board HIT/MISS/NOT-RETRIEVED chips and prevents 6 forced summaries.

**(4) Per-goal abstraction.** For each fired goal, prompt Qwen3-35B with ONLY its ~6-10 retrieved chunks + a facet sub-structure (`prompts.goal_facet`: challenge/approach/outcome) asking a narrow single-facet question.

**(5) Grounding/citation.** Every `supporting_quote` must pass the shared grounding floor (≥6 words / ≥40 chars, verbatim substring) — extracted from `qa.py::_quote_is_grounded` into a shared `services/library/_grounding.py` (graft from #1) so qa, goal-summaries, and quality share ONE substring contract. `key_sections` = exactly the distinct `section_title`s the quotes came from, so the board's "Key Sections" cannot drift from evidence. Reject any non-abstained summary with zero grounded quotes.

### Per-goal output shape (new `models/triage.py::GoalSummary`)

```python
class GoalSummary(BaseModel):
    goal: str
    relevant: bool
    retrieval_state: Literal["hit", "miss", "not_retrieved"]  # graft #1 — MISS guard
    score: float                  # 0-3 fused/reranked
    summary: str | None           # ≤3 sentences, None when abstained
    key_sections: list[str]       # distinct evidence section titles
    supporting_quotes: list[str]  # verbatim spans
    abstained: bool
```

Per-paper output = `list[GoalSummary]` (always length 6 so the board always renders all cells), persisted in `deep_reviews.json` beside the digest.

### Exact reuse / new

- **Reuse verbatim**: `_search._rrf`, `get_reranker`/`Reranker.rerank`, `PaperChunkIndex`/`chunk_text`, `EmbeddingCache._embed`, `qa._paper_context_source`/`_TEXT_CACHE`, `qa._quote_is_grounded`, `paper_render.render_sections`.
- **New**: `_paper_goal_summaries.py` (≤200), `_grounding.py` (small shared helper), `PaperChunkDenseIndex` (in-memory, lives in `_paper_goal_summaries.py`), `goals.yaml prompts.goal_facet`.

**Cost**: 6 cached goal embeddings; gate drops most goals before abstraction; only fired goals (typically 1-3) feed ~8 chunks each → far cheaper than map-reducing the PDF 6×; fits Qwen3-35B local latency. `deep_review` is already opt-in/background with single-flight + JSON cache.

---

# 5. Reference-free quality eval

New `services/library/quality_eval.py` (≤300 LOC) + `services/library/_quality_prompts.py` (≤200 LOC). **`quality_review.py` stays at 74 LOC, untouched** — the rubric logic lands in siblings (Design 3's LOC discipline). Output is a **3-BAND verdict {FLAG / NEUTRAL / HIGHLIGHT}**, NOT a fine score (~3pt human-vs-LLM error makes finer granularity noise). Surfaced as the **Rigor spine chip** + the **Quality Panel**.

### (1) Cheap structural pre-filter (before any LLM pass)

Regex/structure over `render_sections` (graft from #1 — sharper leakage rules): presence of a limitations section, CI/error-bar tokens, code/data URL, ablation section, dataset-provenance statement. Absences = high-precision near-zero-cost flags. **Leakage red flags**: suspiciously-perfect numbers (AUC>0.98 / ~100% with no leakage discussion) and plain random K-fold on temporal/grouped (per-patient) data. Short-circuits obvious flags and seeds the checklist.

### (2) Decomposed yes/no/NA rubric (each item + behavioral anchor + required grounding quote)

Derived 1:1 from the 4 `triage_criteria`:
- **EVAL RIGOR** (crit 1): external/held-out validation? uncertainty (CIs/error bars/multi-seed)? ablation? fair/current baselines? significance test for "best" claims?
- **METHODOLOGY CLARITY** (crit 3): dataset provenance (source/version/license)? preprocessing + split-before-scaling? architecture + training + compute reproducible?
- **REPRODUCIBILITY**: code released? data released or concrete access path? (also fills SS#10 Methods.)
- **OPEN DATASET** (crit 4): provides/uses an open-source agentic-AI benchmark dataset in science/clinical/bioinformatics? `[yes-with-dataset / relevant-no-dataset / off-topic]`.
- **RELEVANCE** (crit 2): handled by the Goal Match Board so quality stays author-blind and goal-agnostic per the ARR split.

### (3) Claim-grounding via faithbench (reuse, don't reinvent)

- Run `_build_claims.decompose_digest` + `_judge.judge_claim` (hard verbatim ladder → soft pinned judge, `PaperChunkIndex` retrieval, two-pass on not_enough_info) over the digest's claim-bearing fields to get a **per-paper claim-grounding pass rate**.
- **Versioned CLAIM_FIELDS** (graft from #1): the live `CLAIM_FIELDS` is exactly 6 fields. Adding `key_findings` + per-goal summaries is an **additive aspect-faithfulness track** with the pinned judge model — version the field set; do **not** mutate the existing QA/claims benchmark numbers.
- **Intra-paper overstatement (RIGOURATE)**: extract the abstract's 2-4 headline claims, retrieve supporting body passages via the same per-paper hybrid index, flag causal-language-without-causal-design / "SOTA"-without-baseline-table / generalization-without-external-data. Surface the specific unsupported claim.

### (4) LLM-judge calibration (glassbox / local-first)

- **Coarse 3-band only**; absolute number distrusted.
- **Self-consistency ×3** at temp>0; commit a band only where runs agree, else "uncertain — human look". Default-discount stated confidence.
- **Reference-exemplar anchoring** (graft from #2, the judges' standout idea): embed ONE known-good + ONE known-weak paper of the **same Keshav category** into the rubric prompt → quasi-relative judgment without N² cost. Exemplar ids wired per category in `goals.yaml`.
- **Bias guards** (free, prompt-level): author/affiliation **blinded**; permute rubric score-option order ×2 and average; ignore length/polish; reuse `<untrusted_input>` sanitization against in-PDF "give a positive review" injection.
- **Calibrate against claim-grounding**: if N% of claims are unsupported/NEI, lower displayed confidence and bias the band toward FLAG.
- **Prestige-adjusted HIGHLIGHT floor** (graft from #1, wired not just stated): reuse `services/model/prestige.py::compute_prestige_score` + `_score_distribution.py::prestige_floor` — below-median-prestige papers must earn **higher** checklist coverage to reach HIGHLIGHT; **never demotes** young/uncited papers below neutral (asymmetric floor only).
- **Domain routing**: clinical/bio (G1/G3/G6) → patient-level split / external validation / calibration / dataset-shift / CLAIM/TRIPOD+AI tag; agentic (G2/G4/G5) → run determinism / eval contamination / autonomy level / policy-enforcement substantiation.
- **Future calibration loop**: validate band thresholds against the existing golden-dataset triage loop (read-vs-skip), track agreement/ECE, recalibrate from empirical base rates.

### New PaperDigest fields

`quality_band: Literal["flag","neutral","highlight"]`, `rubric: dict[str,str]`, `red_flags: list[str]`, `overstatements: list[str]`, `claim_grounding_rate: float`, `quality_confidence: float`, plus the 6 missing summary fields (`executive_summary`, `key_findings`, `methods`, `limitations`, `industry_impact`, `academy_impact`). **Single source of truth**: extend `PaperDigest` (Design 3's decision) rather than leaving `RefinedSummary` to drift — promote its 11-field shape into the digest contract.

---

# 6. Phased implementation plan

Each phase ≤ a few files, ≤500 LOC/file, layering `api→services→storage→models`, README updated in the same commit (CLAUDE.md rule 3). **Layers are independently skippable/cached** in `deep_review` (graft from #2) so a slow/failing layer never blocks the neutral digest.

### Phase 0 — Model + grounding foundation
- **Create** `services/library/_grounding.py`: lift `_quote_is_grounded` + `_MIN_QUOTE_WORDS`/`_MIN_QUOTE_CHARS` out of `qa.py`; **modify** `qa.py` to import from it.
- **Modify** `models/triage.py`: add `GoalSummary`; extend `PaperDigest` with the new quality + summary fields (single source of truth).
- **Verify**: `pytest -q --forked tests/test_library_qa.py`; new `test_grounding.py` (substring floor unchanged); existing qa tests pass unchanged.

### Phase 1 — Reference-free quality eval (the highest-leverage, on-lens v1)
- **Create** `services/library/quality_eval.py` (≤300) + `_quality_prompts.py` (≤200): structural pre-filter + leakage rules, decomposed yes/no/NA rubric, claim-grounding rate via `faithbench` (versioned CLAIM_FIELDS, additive track), RIGOURATE overstatement, self-consistency ×3 + bias guards + exemplar anchoring, prestige floor, 3-band aggregation, domain routing.
- **Reuse**: `quality_review.assess_digest`, `_build_claims.decompose_digest`, `_judge.judge_claim`, `compute_prestige_score`, `prestige_floor`, `_grounding`.
- **Modify** `goals.yaml`: `prompts.quality_rubric`, `prompts.overstatement`, per-category exemplar ids, rubric weights, band thresholds.
- **Verify**: `test_quality_eval.py` (band aggregation, leakage detection, self-consistency abstain, prestige floor never demotes uncited). `pre-commit run --all-files`.

### Phase 2 — Goal-conditioned retrieval summaries
- **Create** `services/library/_paper_goal_summaries.py` (≤200): section-aware chunker, `PaperChunkDenseIndex`, per-goal retrieve→`_rrf`→rerank→gate→facet-abstract loop with the degradation ladder, per-quote grounding, `retrieval_state` resolution.
- **Reuse**: `EmbeddingCache._embed`, `_search._rrf`, `get_reranker`, `PaperChunkIndex`, `qa._paper_context_source`/`_TEXT_CACHE`, `_grounding`, `render_sections`.
- **Modify** `goals.yaml`: `prompts.goal_facet`, goal_summaries config (relevance_floor, top_k, self_consistency_runs).
- **Verify**: `test_paper_goal_summaries.py` (chunks carry section meta; gate fires MISS; NOT-RETRIEVED on degraded retrieval; quotes grounded; key_sections == quote sections).

### Phase 3 — Orchestrator wiring
- **Modify** `deep_review.py::_review_one`: after `assess_digest` (its single call site, line 203), also call `quality_eval` + `_paper_goal_summaries`, persist `{digest, quality_*, goal_summaries}` in the cache entry; each layer wrapped in try/except so failure degrades gracefully.
- **Verify**: cached entry shape; `test_api_routes.py` unchanged.

### Phase 4 — Renderer
- **Create** `services/library/_paper_read_brief.py`: `_brief_section_html` (board + 2 spine chips + verdict), `_goal_board_html` (6-cell grid, 3 states), `_quality_panel_html` (rubric grid + expandable quotes), `_per_goal_section_html`, `_conditional_rigor_html`. CSS for board grid + chip colours + red-flag + NOT-RETRIEVED neutral styling.
- **Modify** `_paper_read_html.py`: `_render_presentation` calls `_brief_section_html`; `_render_notes` gains the new fields. Keep `_paper_read_html.py` < 500 LOC by housing the new helpers in the sibling.
- **Verify**: renderer test for the board (all 6 cells render; MISS vs NOT-RETRIEVED distinct); `cd frontend && npm run build` if UI touched; visual check of one PDF-only and one TeX paper.

### Phase 5 — Faithbench aspect track + READMEs
- **Modify** `faithbench`: version CLAIM_FIELDS additively; per-goal aspect-faithfulness check (evidence entails each goal_summary; abstention fires when goal absent) reusing frozen-text + sha.
- **Modify** READMEs: `services/library/`, `services/faithbench/`, `models/`, `goals.yaml`.
- **Verify**: faithbench existing numbers unchanged (additive track only); full `pre-commit` + `pytest -q --forked` vs baseline.

---

# 7. Open decisions for the user

1. **Per-goal summaries vs. unified relevance paragraph.** This design commits to **per-goal** briefs gated by relevance (the Goal Match Board), which is the whole point of the upgrade — but it costs more local LLM calls per paper (1-3 fired goals × facet abstraction + 3× quality self-consistency) than a single unified relevance paragraph. Confirm you want the per-goal board, or a lighter "top-2 fired goals only" variant to cap latency.

2. **How aggressive the quality FLAG.** The 3-band verdict can either (a) **flag conservatively** — only FLAG on structural absence + high unsupported-claim rate (few false positives, may miss weak preprints), or (b) **flag aggressively** — any single leakage red flag or sub-floor checklist coverage trips FLAG (catches more, more false positives on legitimate preprints). Default proposed: conservative + "uncertain — human look" on self-consistency disagreement. Confirm the aggressiveness, and whether the prestige-adjusted HIGHLIGHT floor should be **on** at launch.

3. **Embedding model for the per-chunk dense leg.** Reuse the resident `EmbeddingCache._embed` model (all-MiniLM-class — cheap, already loaded, zero new RAM) for both goals and chunks? Or pay for a stronger embedder per chunk? The SemEval-2025 caveat — BM25 can add noise with a very strong embedder — means BM25 weight should be tunable regardless. Default: reuse the resident model, keep BM25 weight a config knob.

4. **Self-consistency runs vs. latency.** Quality band defaults to **3×** self-consistency on the single band call only (not per-goal). On slower local setups this triples the quality-eval call. Confirm 3×, or drop to a single pass with confidence-discount-only (cheaper, less reliable on the FLAG/HIGHLIGHT boundary).

---

## Key file references
- Render gap: `zotero_summarizer/services/library/_paper_read_html.py::_digest_section_html` (line 201, flat `relevance` row); renderer at 404/500 LOC → new sibling `_paper_read_brief.py`.
- Models: `zotero_summarizer/models/triage.py::PaperDigest` (line 87, extend), `RefinedSummary` (line 110, 11 unused fields, promote into digest).
- Quality core: `zotero_summarizer/services/library/quality_review.py::assess_digest` (74 LOC, untouched) → new `quality_eval.py` + `_quality_prompts.py`.
- Retrieval: `_search.py::_rrf` (k=60, line 24), `services/model/reranker.py::get_reranker`, `faithbench/_corpus.py::PaperChunkIndex`/`chunk_text` (CHUNK_CHARS=1200/overlap=200/TOP_K=6), `storage/corpus.py::EmbeddingCache._embed`, `storage/corpus_read.py::query_affinity_for_items` (cached-by-item_id — confirms per-chunk dense is net-new) → new `_paper_goal_summaries.py` + `_grounding.py`.
- Grounding: `qa.py::_quote_is_grounded` (line 196), `_paper_context_source`/`_TEXT_CACHE` (line 49-93, reuse the cache).
- Claim track: `faithbench/_build_claims.py::CLAIM_FIELDS` (line 29, exactly 6 — version additively), `_judge.py::judge_claim`.
- Orchestrator: `deep_review.py::_review_one` (line 165; `assess_digest` single call site line 203), `_run_job`, `get_cached_review`.
- Prestige floor: `services/model/prestige.py::compute_prestige_score`, `_score_distribution.py::prestige_floor`.
- Sections substrate: `paper_render.py::build_paper_read_for_pdf` (line 253; `render_sections` line 280; PDF-only `"Page N"` fallback `_paper_read_pdf.py` line 283).
