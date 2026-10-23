#!/bin/sh
# Usage: tools/create-pr.sh 'PR title' /path/to/body.md
set -eu

if [ "$#" -ne 2 ] || [ ! -f "$2" ] || [ ! -r "$2" ]; then
    echo "Usage: tools/create-pr.sh 'PR title' /path/to/readable-body.md" >&2
    exit 2
fi
body=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
cd "$(dirname "$0")/.."
branch=$(git symbolic-ref --quiet --short HEAD)
if [ "$branch" = main ]; then
    echo "Use a dedicated PR branch, not main." >&2
    exit 1
fi
head=$(git rev-parse HEAD)
origin=$(git remote get-url origin)

require_checked_source() {
    status=$(git status --porcelain --untracked-files=all)
    current_head=$(git rev-parse HEAD)
    current_branch=$(git symbolic-ref --quiet --short HEAD)
    if [ -n "$status" ] || [ "$current_head" != "$head" ] || [ "$current_branch" != "$branch" ]; then
        echo "Commit your changes first; source/branch must stay unchanged during checks. Nothing was stashed or reverted." >&2
        exit 1
    fi
}

require_checked_source
# Inherited dry-run/ignore-error Make flags must not bypass the gate.
MAKEFLAGS= MFLAGS= GNUMAKEFLAGS= make check
require_checked_source
git push origin "$head:refs/heads/$branch"
require_checked_source
exec gh pr create --repo "$origin" --base main --head "$branch" --title "$1" --body-file "$body"
