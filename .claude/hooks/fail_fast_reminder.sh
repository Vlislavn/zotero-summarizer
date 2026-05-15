#!/usr/bin/env bash
# Inject a short fail-fast reminder before Edit/Write/NotebookEdit on Python files.
# Per IVAI-D001 (scripts/lint_ivai.py) and .github/instructions/python.instructions.md.

set -euo pipefail

input="$(cat)"
file_path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // ""')"

case "$file_path" in
  *.py|*.pyi) ;;
  *) exit 0 ;;
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
Also: make sure files are <500 LOC strict, not more than 3 levels deep, and have a clear single responsibility. If not, refactor first before adding complexity.
EOF
)" '{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": $msg
  }
}'
