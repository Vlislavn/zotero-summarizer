#!/usr/bin/env bash
# Memory-gated launcher for the local MLX deep-review model.
#
# Brings up mlx_lm.server (the Qwen3.6-35B) at :8080 ONLY when there is RAM
# headroom, with a tight KV-cache cap, single-instance, after freeing ollama — so
# the `deep_review` stage gets MLX's fast prefill (~16s for a 16k-token prompt vs
# ~205s on ollama) WITHOUT blowing the 48 GB box. The 2026-06-12 panic was the
# 35B's unbounded growing prompt cache + stacking heavy work; this caps the cache
# and refuses to load into a full machine.
#
# Foreground + supervised (Ctrl-C to stop), single instance — per the Apple-
# silicon hardware-safety rule. feed/backlog stay on ollama, so the feed daemon
# keeps working whether or not this is running.
#
# Usage:   tools/mlx-deep-review.sh
# Tunables (env): MLX_MIN_FREE_GB (default 26), PROMPT_CACHE_BYTES (4G),
#                 PROMPT_CACHE_SIZE (2), MLX_PORT (8080), IVAI_SERVE (path to serve.sh)
set -euo pipefail

MLX_MIN_FREE_GB="${MLX_MIN_FREE_GB:-26}"
MLX_PORT="${MLX_PORT:-8080}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
IVAI_SERVE="${IVAI_SERVE:-$HOME/code/personal/IVAI/scripts/mlx/serve.sh}"
# Tight KV-cache caps (override serve.sh's 12G default) so the footprint stays
# ~20 GB weights + <=4 GB cache. Exported for serve.sh to read.
export PROMPT_CACHE_BYTES="${PROMPT_CACHE_BYTES:-4G}"
export PROMPT_CACHE_SIZE="${PROMPT_CACHE_SIZE:-2}"
export PORT="$MLX_PORT"

# 1. Single instance — never run two 35B servers.
if curl -sf -m 2 "http://127.0.0.1:${MLX_PORT}/v1/models" >/dev/null 2>&1; then
  echo "MLX is already serving on :${MLX_PORT} — nothing to do."
  exit 0
fi

# 2. Make room FIRST — unload any resident ollama models (frees up to ~13 GB) so
# the RAM gate below reflects the memory MLX will actually have. The feed daemon
# reloads ollama on its next tick; best-effort, so a down ollama can't abort.
LOADED="$(curl -sf -m 3 "${OLLAMA_URL}/api/ps" 2>/dev/null \
  | python3 -c "import json,sys; print(' '.join(m['name'] for m in json.load(sys.stdin).get('models', [])))" 2>/dev/null || true)"
for m in ${LOADED}; do
  echo "freeing ollama model: ${m}"
  curl -sf -m 5 "${OLLAMA_URL}/api/generate" -d "{\"model\":\"${m}\",\"keep_alive\":0}" >/dev/null 2>&1 || true
done
[[ -n "${LOADED}" ]] && sleep 2 || true

# 3. RAM gate — the "don't blow memory" guarantee, checked AFTER freeing ollama.
# Uses AVAILABLE memory (free + inactive + speculative + purgeable — what the OS
# can actually hand out), not memory_pressure's optimistic free-% (which counts
# compressible memory and would let the 35B trigger heavy swap).
read -r TOTAL_GIB AVAIL_GIB < <(python3 - <<'PY'
import re, subprocess
memsize = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip())
pagesize = int(subprocess.check_output(["sysctl", "-n", "vm.pagesize"]).strip())
vm = subprocess.check_output(["vm_stat"]).decode()
def pages(name: str) -> int:
    m = re.search(rf"{name}:\s+(\d+)", vm)
    return int(m.group(1)) if m else 0
avail = (pages("Pages free") + pages("Pages inactive")
         + pages("Pages speculative") + pages("Pages purgeable")) * pagesize
print(memsize // 1024**3, avail // 1024**3)
PY
)
echo "RAM: ${AVAIL_GIB} GiB available of ${TOTAL_GIB} GiB (need >= ${MLX_MIN_FREE_GB})"
if (( AVAIL_GIB < MLX_MIN_FREE_GB )); then
  echo "ABORT: only ${AVAIL_GIB} GiB available; the 35B needs ~${MLX_MIN_FREE_GB} GiB" >&2
  echo "       (~20 GB weights + <=4 GB KV cache + margin). Close apps and retry," >&2
  echo "       or lower the bar at your own risk: MLX_MIN_FREE_GB=20 $0" >&2
  exit 1
fi

# 4. Launch the cache-capped canonical server (patched non-thinking template).
if [[ ! -f "${IVAI_SERVE}" ]]; then
  echo "ABORT: MLX server launcher not found at ${IVAI_SERVE} — set IVAI_SERVE=/path/to/serve.sh" >&2
  exit 1
fi
echo "starting MLX on :${MLX_PORT} (PROMPT_CACHE_BYTES=${PROMPT_CACHE_BYTES}, PROMPT_CACHE_SIZE=${PROMPT_CACHE_SIZE}) — Ctrl-C to stop"
exec bash "${IVAI_SERVE}"
