#!/bin/sh
# `make iphone`: serve this checkout to your own tailnet devices at
# https://<this-mac>.<tailnet>.ts.net. Tailscale Serve terminates HTTPS and adds the
# signed-in login; `serve-mobile` admits only that owner (docs/features.md#remote-access).
# The app uses the same port as `make run`, so only one copy can use the data at a time.
# Ctrl+C stops the app and removes the HTTPS:443 mapping this script created.
set -eu
ZS=${ZS:-zotero-summarizer} PY=${PY:-python3} TAILSCALE=${TAILSCALE:-tailscale} PORT=${PORT:-8000}

status=$("$TAILSCALE" status --json 2>/dev/null) || {
  echo "Tailscale CLI unavailable: install/open the Tailscale app and sign in." >&2
  exit 2
}
identity=$(printf '%s' "$status" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
if d.get("BackendState") != "Running":
    sys.exit("Tailscale is not connected: open the Tailscale app and sign in.")
if not d.get("CertDomains"):
    sys.exit("Enable HTTPS: Tailscale admin console -> DNS -> HTTPS Certificates.")
me = d["Self"]
print("https://" + me["DNSName"].rstrip("."), d["User"][str(me["UserID"])]["LoginName"])
') || exit 2
set -- $identity
origin=$1 owner=$2

trap 'exit 130' INT TERM
trap '"$TAILSCALE" serve --https=443 off >/dev/null 2>&1 || true' EXIT
"$TAILSCALE" serve --bg --https=443 "http://127.0.0.1:$PORT" >/dev/null
echo "Open $origin on your iPhone or this Mac (Tailscale on, signed in as $owner). Ctrl+C stops."
"$ZS" serve-mobile --origin "$origin" --owner-login "$owner" --trusted-loopback-port "$PORT"
