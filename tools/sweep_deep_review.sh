#!/usr/bin/env bash
# Memory-SAFE deep-review model sweep driver. Foreground, single-instance,
# lightest-first — mirrors tools/mlx-deep-review.sh's hardware-safety discipline.
#
# Runs the config matrix ONE config at a time as separate bench processes (RAM
# released between), each persisted under data/deep_review_sweep/runs/<id>/ with a
# headline in runs-index.jsonl. Phase 1 (cloud sota text-budget sweep) is memory-
# safe and runs unconditionally; Phase 2 (local models) is gated on a fresh
# free-physical-% check before EACH config and the bench's own in-process tripwire
# (free-phys% + swap-growth) aborts mid-run if the box starts thrashing.
#
# Usage (foreground, supervised, box should be idle for Phase 2):
#   tools/sweep_deep_review.sh                 # full sweep
#   PHASES=1 tools/sweep_deep_review.sh        # cloud budget sweep only (always safe)
#   PHASES=2 tools/sweep_deep_review.sh        # local model sweep only
#   PAPERS=4NIMLFMV,QRPEWC69 tools/sweep_deep_review.sh   # fewer papers / faster
#
# Env overrides: PAPERS, REF_PROVIDER, REF_MODEL, LEAN (local budget chars),
# FULL (sota full budget), MIN_FREE_PCT (skip a local config below this free-phys%),
# CANDIDATES (space-separated local ollama models), PHASES (1|2|both).
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env 2>/dev/null || true; set +a

PAPERS="${PAPERS:-4NIMLFMV,QRPEWC69,R2HRV4JA,YJQWHD6X}"
REF_PROVIDER="${REF_PROVIDER:-kather}"
REF_MODEL="${REF_MODEL:-sota}"
LEAN="${LEAN:-12000}"          # local lean tier (production); 60k thrashed the box
FULL="${FULL:-60000}"         # sota full-tier reference budget
MIN_FREE_PCT="${MIN_FREE_PCT:-12}"
CANDIDATES="${CANDIDATES:-qwen3.5:0.8b qwen3.5:4b qwen3.5:4b-mxfp8}"  # lightest first
PHASES="${PHASES:-both}"

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
  echo "=== PHASE 1: sota text-budget sweep (cloud — memory-safe) — does a smaller budget hold quality? ==="
  run "sota_budget_${LEAN}" \
      --reference-provider "$REF_PROVIDER" --reference-model "$REF_MODEL" --reference-thinking on --reference-max-chars "$FULL" \
      --candidate-provider "$REF_PROVIDER" --candidate-model "$REF_MODEL" --candidate-thinking on --candidate-max-chars "$LEAN"
  run "sota_budget_30000" \
      --reference-provider "$REF_PROVIDER" --reference-model "$REF_MODEL" --reference-thinking on --reference-max-chars "$FULL" \
      --candidate-provider "$REF_PROVIDER" --candidate-model "$REF_MODEL" --candidate-thinking on --candidate-max-chars 30000
fi

if [ "$PHASES" = "2" ] || [ "$PHASES" = "both" ]; then
  echo; echo "=== PHASE 2: local model sweep @ ${LEAN} chars (sota@${LEAN} reference, both digest thinking-on) ==="
  for M in $CANDIDATES; do
    safe_name="local_$(echo "$M" | tr ':.' '__')_${LEAN}"
    if mem_gate; then
      run "$safe_name" \
        --reference-provider "$REF_PROVIDER" --reference-model "$REF_MODEL" --reference-thinking on --reference-max-chars "$LEAN" \
        --candidate-provider default --candidate-model "$M" --candidate-thinking on --candidate-max-chars "$LEAN"
    else
      echo "  SKIP ${M} — free-phys below ${MIN_FREE_PCT}% (box loaded). Free RAM / close apps, then re-run; resume is automatic."
    fi
  done
fi

echo; echo "=== sweep complete. Headlines: data/deep_review_sweep/runs-index.jsonl ==="
echo "Compare runs:  python3 -c \"import json;[print(l['run_id'],l['candidate'],'q=%.1f/%.1f'%(l['candidate_quality_mean'],l['reference_quality_mean']),'t=%.0fs'%l['candidate_secs_mean'],'parity',l['quality_parity']) for l in map(json.loads, open('data/deep_review_sweep/runs-index.jsonl'))]\""
