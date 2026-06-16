# tools/ — developer scripts (run via `uv run`, not shipped)

One-off eval / ops scripts. Not imported by the app; each has a module docstring with usage.

| script | what it does |
|---|---|
| `bench_deep_review.py` | benchmark a deep-review DIGEST model (candidate) vs a SOTA reference on quality + time + memory, judged blinded/pairwise by a pinned independent LLM judge. `--run-name` persists a versioned, resumable run under `data/deep_review_sweep/`. See **[docs/benchmarking.md](../docs/benchmarking.md)**. |
| `sweep_deep_review.sh` | memory-SAFE driver that runs the bench config matrix one config at a time (Phase 1 cloud budget sweep, Phase 2 local models lightest-first behind a free-phys-%/swap-growth gate). Foreground, single-instance — mirrors `mlx-deep-review.sh`. |
| `mlx-deep-review.sh` | launch the local MLX server (Qwen3.6-35B) foreground with a RAM gate before pointing `deep_review` at it. **Never** run on a loaded box (22 GB weights). |
| `eval_goal_embedder.py` | offline eval of the goal-similarity embedder. |
| `eval_slate_blend.py` | offline eval of the Today-slate ranking blend. |
| `eval_temporal_objective.py` | offline eval of the temporal-split training objective. |
| `validate_prestige_upgrade.py` | sanity-check the OpenAlex prestige enrichment. |
| `precommit/` | the repo's custom pre-commit checks (LOC cap, layering, README freshness, dead-code, AI-slop). |

**Benchmarking discipline + the memory-safety protocol live in [docs/benchmarking.md](../docs/benchmarking.md)** — read it before running any local sweep (this box has been thrashed by unsupervised local benchmarking).
