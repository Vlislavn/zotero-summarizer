#!/usr/bin/env bash
# Inject fail-fast and simplification context before a Python edit.

set -euo pipefail

input="$(cat)"
file_path="$(printf '%s' "$input" | jq -r '
  if (.tool_input | type) == "object" then .tool_input.file_path // "" else "" end
')"
patch="$(printf '%s' "$input" | jq -r '
  if (.tool_input | type) == "string" then .tool_input
  elif (.tool_input | type) == "object" then .tool_input.patch // .tool_input.input // ""
  else "" end
')"

case "$file_path" in
  *.py|*.pyi) ;;
  *) printf '%s' "$patch" | grep -Eq '\.pyi?([[:space:]:]|$)' || exit 0 ;;
esac

jq -n --arg msg "$(cat <<'EOF'
FAIL-FAST REMINDER 
Before writing this file, verify the code:
- NO bare `except:` or `except Exception:` without re-raise
- NO `try/except: pass` (silent swallowing)
- NO default-on-error fallbacks (e.g. `return None` after a failed call, `or default` masking errors)
- NO "best-effort" / graceful-degradation patterns UNLESS the user explicitly asked for them
- Validate at I/O boundaries; trust internal happy paths
- Errors are signals — let them propagate

If a fallback is genuinely required (boundary contract, user request), keep it narrow and document the user instruction that authorized it.

CODE-IN-HEAD SIMPLIFICATION
- Use the `code-that-fits-in-your-head` skill for every code change.
- Trace callers and search for an existing helper before writing.
- Prefer deletion, reuse, or a direct stdlib/platform feature over new code or abstractions.
- Keep responsibilities cohesive; do not split only to game a metric.
- Hard limits: every Python file <=500 LOC; production function body <=88 code lines;
  <=6 required parameters; control flow <=5 levels deep.
- Before finishing, inspect the touched code for duplicated branches, dead paths,
  unnecessary indirection, and nearby duplication. Simplify now when safe.
- State the simplification assessment explicitly in the final response.
EOF
)" '{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": $msg
  }
}'
