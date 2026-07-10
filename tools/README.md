# tools/ — developer scripts (run via `uv run`, not shipped)

One-off eval / ops scripts. Not imported by the app; each has a module docstring with usage.

| script | what it does |
|---|---|
| `bench_deep_review.py` | benchmark a deep-review DIGEST model (candidate) vs a strong reference on quality + time + memory, judged blinded/pairwise by a pinned independent LLM judge. `--run-name` persists a versioned, resumable run under `data/deep_review_sweep/`. See **`docs/benchmarking.md`** (local-only, gitignored). |
| `sweep_deep_review.sh` | memory-SAFE driver that runs the bench config matrix one config at a time (Phase 1 cloud budget sweep, Phase 2 local models lightest-first behind a free-phys-%/swap-growth gate). Foreground, single-instance — mirrors `mlx-deep-review.sh`. |
| `mlx-deep-review.sh` | launch the local MLX server (Qwen3.6-35B) foreground with a RAM gate before pointing `deep_review` at it. **Never** run on a loaded box (22 GB weights). |
| `bench_gliner2.py` | **Phase-0 go/no-go for GLiNER2** (Fastino zero-shot encoder, `--extra entities`) BEFORE any product wiring: `--probes` selects `type` (0a: paper-type classification vs `gold_v1`, baseline = LLM type accuracy from `bench_paper_quality.py`), `ner` (0b: disease NER; `--ner-dataset bc5cdr` streams the real BC5CDR test set, the inline fixture is a wiring floor only), `params` (0c: `extract_json` of the 6 `PaperParameters` fields on the gold abstracts + an abstention check — persists `params_encoder.jsonl` for the agreement join). Leaf tool (no product import). `--selfcheck` validates the logic with no model download. |
| `_gliner2_lib.py` | model-free pure graders (stats, NER set-F1, PaperParameters parse/agreement) shared by `bench_gliner2` + the two LLM baselines so both sides score with the SAME grader. stdlib only; unit-checked by `bench_gliner2 --selfcheck`. |
| `bench_llm_ner.py` | the LLM 'against what' for `bench_gliner2` 0b: hosted-LLM (api.kather.ai) zero-shot disease NER on the SAME BC5CDR sentences + SAME grader — the 205M-encoder-vs-LLM head-to-head. Needs `CUSTOM_API_KEY`. |
| `bench_llm_params.py` | the LLM baseline + agreement scorer for `bench_gliner2` 0c: runs the SAME 6-field schema through a hosted LLM (api.kather.ai) on the same gold abstracts, joins `params_encoder.jsonl`, reports per-field presence/value agreement + encoder over-emission + abstention (no independent params gold exists → agreement-with-the-incumbent-LLM is the decision metric). Needs `CUSTOM_API_KEY`. |
| `eval_goal_embedder.py` | offline eval of the goal-similarity embedder. |
| `eval_slate_blend.py` | offline eval of the Today-slate ranking blend. |
| `eval_temporal_objective.py` | offline eval of the temporal-split training objective. |
| `eval_quality_promote.py` | offline eval of the quality→must_read promotion (`rank_blend.promote_band`) against firewalled user verdicts — precision + flooding per (goal, relevance) floor. Gates the `quality_promote` flip. |
| `validate_prestige_upgrade.py` | sanity-check the OpenAlex prestige enrichment. |
| `precommit/` | the repo's custom pre-commit checks (LOC cap, layering, README freshness, dead-code, AI-slop). |

**Benchmarking discipline + the memory-safety protocol live in `docs/benchmarking.md` (local-only, gitignored — not in the repo)** — read it before running any local sweep (this box has been thrashed by unsupervised local benchmarking).
