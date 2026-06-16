# Deep-review model benchmarking — methodology + how to store runs

How we compare a deep-review DIGEST model (local or cloud) against a SOTA reference on
**quality, time, and memory** — and persist every run reproducibly. The digest is the
user-facing referee output; this harness answers "which model/budget/thinking config gives
the best digest per second of wall-clock, within the box's memory budget."

```
                 ┌── deterministic FIRST ──┐   ┌──── then the LLM judge ────┐
 paper qa_text → │ field-completeness (no  │ → │ pinned, INDEPENDENT, blinded│ → quality
   (cached)      │ LLM): empty fields = a  │   │ pairwise A/B (order random) │   + TIME +
                 │ thinking-off red flag   │   │ scores faithfulness/insight/│   memory
                 └─────────────────────────┘   │ completeness 1-10, picks win│
                                                └─────────────────────────────┘
```

## Tools

| file | role |
|---|---|
| `tools/bench_deep_review.py` | one candidate-vs-reference run: gen both digests (same budget = isolate model), deterministic field-completeness, then the pinned judge scores them blinded/pairwise; reports quality, time (mean+median), and quality-per-minute |
| `tools/sweep_deep_review.sh` | runs the config matrix one config at a time (RAM released between), each persisted; Phase 1 cloud-safe budget sweep, Phase 2 local models lightest-first behind a memory gate |

## Eval discipline (non-negotiable, mirrors `services/faithbench`)

- **Pinned, independent judge.** Default `Qwen3.5-397B-A17B-FP8` on the kather endpoint —
  a different family/size from both candidate and reference. `--judge-provider` can swap in a
  LOCAL judge (e.g. ollama) when the cloud budget is exhausted; such runs are **indicative
  only** (a small local judge is weaker and, if it equals the reference model, self-preference-
  biased — flag them).
- **Deterministic check before the LLM judge** (`are/hard-before-soft-judge`): per-digest
  field-completeness (fraction of substantive PaperDigest fields non-empty) is computed without
  any model — it catches the "thinking-off emits an empty digest" failure for free.
- **Blinded pairwise.** Candidate and reference digests are shown as A/B with the order
  randomised per paper (seeded) so the judge can't position-bias.
- **TIME is co-equal, never collapsed into quality.** Report digest latency mean+median+range
  per arm AND a `quality-per-minute` efficiency — separately (the standing eval rule). Grounded
  in ARE's `run_duration`-per-trial discipline.
- **Mean AND median**, per-dimension. Malformed digests are RECORDED (a hard quality loss),
  not hidden — `_gen_digest` retries twice then records `None`.

## Config axes (what the sweep varies)

```
model            local: qwen3.5:0.8b / qwen3.5:4b / qwen3.5:4b-mxfp8 (ollama) ; cloud: kather sota
text budget      12k (lean / production) · 30k · 60k (full)
digest thinking  ON  — proven NEEDED (thinking-off → empty or hallucinated digests)
prompt           anti-fabrication (only verbatim numbers/URLs; empty beats invented)
```

Settled by measurement this session (do not re-litigate):
- **Thinking is binary for the cloud reference.** `reasoning_effort` and `thinking_budget` are
  silently ignored by kather sota (think_chars ~3200-3500 across all variants) — no middle tier.
- **Local time is PREFILL-bound** → the time lever is the text budget, NOT thinking (qwen3:8b
  digest 60k→93s vs 12k→53s; thinking on/off both ~93s). Cloud sota is thinking-bound.
- **Selective thinking** (digest reasons, trivial calls don't) is the shipped production policy.

## Memory-safety protocol (LOAD-BEARING — local benchmarking thrashed this box + killed ollama)

This is a 48 GB Apple-silicon Mac. Heavy local benchmarking once drove swap to ~9 GB and killed
ollama mid-run. Therefore:

- **Foreground, single-instance, ONE config at a time. NEVER background. NEVER MLX-35B while
  loaded** (22 GB weights). `tools/mlx-deep-review.sh` is the only sanctioned MLX path and is
  itself foreground + RAM-gated.
- **Startup PRE-FLIGHT on absolute swap** (`--max-swap-start-mb`, default 6000): refuse to begin a
  run that loads ANY local model when swap is already high. Learned the hard way (2026-06-16): a
  local *reference* digest (qwen3:8b, 5 GB) loading on a box already at 10 GB swap pushed swap
  **+4.5 GB in seconds** — and the per-paper gate never fired because it sits BETWEEN the
  reference and candidate gens, after the reference already thrashed. The pre-flight refuses up
  front (verified: aborts, loads no model).
- **Per-gen tripwire keys on free-physical-% + swap-GROWTH, never absolute free-swap** — macOS
  grows the swapfile under pressure so "free swap" stays positive while the box thrashes. BUT
  note `_free_phys_pct()` (free+inactive+speculative ÷ total) reads **falsely healthy** when the
  box is over-committed: it counted 31.7 % "free" while *truly-free* was 151 MB and swap was
  10 GB — which is exactly why the absolute-swap PRE-FLIGHT is the load-bearing gate, not
  free-phys-%. The per-gen gates (`--min-free-phys-pct`, `--max-swap-growth-mb`) are the
  second line; the driver re-checks before each config.
- **Lightest model first** (0.8b → 4b → 4b-mxfp8); **lean 12k** budget for local.
- If the box is loaded, only Phase 1 (cloud) runs; the local sweep waits and **resumes**
  automatically (it skips papers already in the run store).

## Results store (reproducible, versioned — "how to save the benchmarkings")

Mirrors the faithbench run-dir pattern; reuses `services/run_log.py`
(`make_run_id` / `short_git_commit` / `file_sha256` / `append_run`); lives under `data/`
(gitignored):

```
data/deep_review_sweep/
  runs/<YYYYMMDD_HHMMSS_name>/
    manifest.json   reference{provider,model,thinking,max_chars}, candidate{…}, judge_model,
                    papers, seed, git_commit, goals_yaml_sha, started_at
    papers.jsonl    one PaperResult per line (scores, secs, completeness, winner, reason,
                    malformed counts, full judge_raw) — written INCREMENTALLY (abort-safe)
  runs-index.jsonl  append-only headline per run: parity, quality mean+median, secs, q/min, wins
```

Pass `--run-name NAME` to persist. Re-running the same run-id **resumes** (skips completed
papers — survives a memory abort / Ctrl-C).

## How to run

```bash
# Cloud budget sweep only (always memory-safe) — needs kather budget:
PHASES=1 tools/sweep_deep_review.sh

# Local model sweep, lightest-first, memory-gated (box should be IDLE):
PHASES=2 tools/sweep_deep_review.sh

# Local-only with a LOCAL judge (when the cloud budget is exhausted — indicative):
uv run python tools/bench_deep_review.py --run-name local_q35_4b \
  --reference-provider default --reference-model qwen3:8b   --reference-thinking on  --reference-max-chars 12000 \
  --candidate-provider default --candidate-model qwen3.5:4b --candidate-thinking on  --candidate-max-chars 12000 \
  --judge-provider default --judge-model qwen3:8b --papers 4NIMLFMV,QRPEWC69,R2HRV4JA,YJQWHD6X

# Read the leaderboard:
python3 -c "import json;[print(l['run_id'],l['candidate'],'q=%.1f/%.1f'%(l['candidate_quality_mean'],l['reference_quality_mean']),'parity',l['quality_parity'],'t=%.0fs'%l['candidate_secs_mean']) for l in map(json.loads, open('data/deep_review_sweep/runs-index.jsonl'))]"
```

## Known blockers (operational, 2026-06-16)
- **kather budget exhausted** ($1000 cap) — blocks the sota reference + 397B judge AND
  production deep_review (same account). Restore the budget, or route deep_review at OpenRouter
  / local, before the cloud sweep or production reviews run.
- **box memory** — the local sweep needs an idle box; the gate refuses to start otherwise.

See `docs/architecture.md` for the system map and `zotero_summarizer/services/faithbench/README.md`
for the judge discipline this harness inherits.
