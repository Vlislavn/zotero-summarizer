#!/usr/bin/env bash
# Memory-SAFE deep-review model sweep driver. Foreground, single-instance,
# lightest-first — mirrors tools/mlx-deep-review.sh's hardware-safety discipline.
#
# Runs the config matrix ONE config at a time as separate bench processes (RAM
# released between), each persisted under data/deep_review_sweep/runs/<id>/ with a
# headline in runs-index.jsonl. Phase 1 (cloud reference text-budget sweep) is memory-
# safe and is selected by default; Phase 2 (local models) is gated on a fresh
# free-physical-% check before EACH config and the bench's own in-process tripwire
# (free-phys% + swap-growth) aborts mid-run if the box starts thrashing.
#
# Usage (foreground, supervised, box should be idle for Phase 2):
#   PAPERS="$PAPER_KEYS" REF_PROVIDER="$PROVIDER_NAME" REF_MODEL="$BENCH_MODEL" tools/sweep_deep_review.sh
#   PHASES=1 tools/sweep_deep_review.sh        # cloud budget sweep only (always safe)
#   PHASES=2 CANDIDATE_PROVIDER="$LOCAL_PROVIDER" CANDIDATES="$LOCAL_MODELS" tools/sweep_deep_review.sh
#   PAPERS="$PAPER_KEYS" tools/sweep_deep_review.sh   # fewer papers / faster
#
# Env overrides: PAPERS, REF_PROVIDER, REF_MODEL, LEAN (local budget chars),
# FULL (reference full budget), MIN_FREE_PCT (skip a local config below this free-phys%),
# CANDIDATES (space-separated local models), CANDIDATE_PROVIDER, PHASES (1|2|both; default 1).
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env 2>/dev/null || true; set +a

: "${PAPERS:?Set PAPERS to comma-separated paper keys}"
: "${REF_PROVIDER:?Set REF_PROVIDER to your configured provider name}"
: "${REF_MODEL:?Set REF_MODEL to the served model ID}"
LEAN="${LEAN:-12000}"          # local lean tier (production); 60k thrashed the box
FULL="${FULL:-60000}"         # full-tier reference budget
MIN_FREE_PCT="${MIN_FREE_PCT:-12}"
CANDIDATES="${CANDIDATES:-}"  # caller-selected models, lightest first
PHASES="${PHASES:-1}"
case "$PHASES" in
  1) ;;
  2|both)
    : "${CANDIDATES:?Set CANDIDATES explicitly for the local sweep}"
    : "${CANDIDATE_PROVIDER:?Set CANDIDATE_PROVIDER to your local provider name}"
    ;;
  *) echo "PHASES must be 1, 2, or both" >&2; exit 2 ;;
esac

mem_gate() {  # exit 0 if safe to start a local gen, 1 otherwise; prints status
  python3 - "$MIN_FREE_PCT" <<'PY'
import subprocess, re, sys
total = int(subprocess.run(["sysctl","-n","hw.memsize"], capture_output=True, text=True).stdout)
vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
page = int(re.search(r"page size of (\d+)", vm).group(1))
free = sum(int(m) for m in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)\.", vm))
pct = round(100.0 * free * page / total, 1)
swap = subprocess.run(["sysctl","-n","vm.swapusage"], capture_output=True, text=True).stdout.strip()
print(f"  [mem] free-phys={pct}%  {swap}")
sys.exit(0 if pct >= float(sys.argv[1]) else 1)
PY
}

run() {  # run <run-name> <extra bench args...>
  local name="$1"; shift
  echo; echo "######## ${name} ########"
  uv run python tools/bench_deep_review.py --run-name "$name" --papers "$PAPERS" "$@" \
    || echo "  !! config ${name} exited non-zero (continuing sweep)"
}

if [ "$PHASES" = "1" ] || [ "$PHASES" = "both" ]; then
  echo "=== PHASE 1: reference text-budget sweep (cloud — memory-safe) — does a smaller budget hold quality? ==="
  run "reference_budget_${LEAN}" \
      --reference-provider "$REF_PROVIDER" --reference-model "$REF_MODEL" --reference-thinking on --reference-max-chars "$FULL" \
      --candidate-provider "$REF_PROVIDER" --candidate-model "$REF_MODEL" --candidate-thinking on --candidate-max-chars "$LEAN"
  run "reference_budget_30000" \
      --reference-provider "$REF_PROVIDER" --reference-model "$REF_MODEL" --reference-thinking on --reference-max-chars "$FULL" \
      --candidate-provider "$REF_PROVIDER" --candidate-model "$REF_MODEL" --candidate-thinking on --candidate-max-chars 30000
fi

if [ "$PHASES" = "2" ] || [ "$PHASES" = "both" ]; then
  echo; echo "=== PHASE 2: local model sweep @ ${LEAN} chars (reference@${LEAN} reference, both digest thinking-on) ==="
  for M in $CANDIDATES; do
    safe_name="local_$(echo "$M" | tr ':.' '__')_${LEAN}"
    if mem_gate; then
      run "$safe_name" \
        --reference-provider "$REF_PROVIDER" --reference-model "$REF_MODEL" --reference-thinking on --reference-max-chars "$LEAN" \
        --candidate-provider "$CANDIDATE_PROVIDER" --candidate-model "$M" --candidate-thinking on --candidate-max-chars "$LEAN"
    else
      echo "  SKIP ${M} — free-phys below ${MIN_FREE_PCT}% (box loaded). Free RAM / close apps, then re-run; resume is automatic."
    fi
  done
fi

echo; echo "=== sweep complete. Headlines: data/deep_review_sweep/runs-index.jsonl ==="
echo "Compare runs:  python3 -c \"import json;[print(l['run_id'],l['candidate'],'q=%.1f/%.1f'%(l['candidate_quality_mean'],l['reference_quality_mean']),'t=%.0fs'%l['candidate_secs_mean'],'parity',l['quality_parity']) for l in map(json.loads, open('data/deep_review_sweep/runs-index.jsonl'))]\""
