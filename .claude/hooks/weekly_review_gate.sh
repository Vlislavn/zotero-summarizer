#!/usr/bin/env bash
# SessionStart hook: nudge once/week to run /weekly-review (the paper-gap review).
# ponytail: 7-day window + once/day throttle, literals at top. Two-file state so
# the hook and the /weekly-review command never write the same file (no clobber):
#   data/weekly_review.json  — Claude-owned; we only READ .last_done_at
#   data/.weekly_nudge       — hook-owned; we read/write .last_nudged_at
# Contract: claude-code SessionStart stdin {cwd, source, ...} → stdout
# {"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":…}}.

set -euo pipefail

WEEK_SECONDS=604800   # 7 days

input="$(cat)"
cwd="$(printf '%s' "$input" | jq -r '.cwd // ""')"
[ -n "$cwd" ] || exit 0   # no cwd → nothing to key off; stay silent

done_file="$cwd/data/weekly_review.json"
nudge_file="$cwd/data/.weekly_nudge"

today="$(date -u +%Y-%m-%d)"
now_epoch="$(date -u +%s)"

# Already nudged today? Stay silent (once/day throttle) regardless of due-ness.
if [ -f "$nudge_file" ]; then
  last_nudged="$(jq -r '.last_nudged_at // ""' "$nudge_file" 2>/dev/null || echo "")"
  [ "$last_nudged" = "$today" ] && exit 0
fi

# Last completed review → epoch (missing/unparseable ⇒ never done ⇒ epoch 0).
last_done_at=""
[ -f "$done_file" ] && last_done_at="$(jq -r '.last_done_at // ""' "$done_file" 2>/dev/null || echo "")"
last_epoch=0
if [ -n "$last_done_at" ]; then
  last_epoch="$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$last_done_at" +%s 2>/dev/null || echo 0)"
fi

# Due when a full week has elapsed since the last completed review (or never).
if [ "$((now_epoch - last_epoch))" -lt "$WEEK_SECONDS" ]; then
  exit 0
fi

last_label="never"
[ -n "$last_done_at" ] && last_label="$last_done_at"

# Stamp today's nudge (hook-owned file) so we only nudge once today.
printf '{"last_nudged_at": "%s"}\n' "$today" > "$nudge_file"

jq -n --arg msg "Weekly paper-gap review is due (last done: $last_label). Offer to run /weekly-review — screen each goals.yaml topic via Targeted Search, curate the top papers, and flag which are missing from the Zotero library." '{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": $msg
  }
}'
