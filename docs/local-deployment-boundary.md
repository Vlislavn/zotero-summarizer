# Local app and explicit authenticated gateway

```text
serve / make api -> loopback transport + local Host/Origin -> existing SPA/API
serve-mobile    -> approved Tailscale HTTPS -> private Unix socket
                                         -> exact configured owner -> same SPA/API
```

## Default local use

`zotero-summarizer serve` retains the local app and existing port/reload options.
`localhost` and literal loopback addresses (including mapped IPv4 loopback) work;
wildcard, LAN and arbitrary DNS hosts fail before launch. Proxy-header processing
is disabled. Every API, docs, SPA, asset, public-file and WebSocket request crosses
local peer/Host/Origin/Fetch-Metadata admission. Duplicate security headers fail.
Fixed refusals do not echo supplied identities or headers. A server that rewrites
transport facts or an unauthenticated loopback proxy is outside this boundary.

`make iphone` no longer discovers a LAN address, starts a public HTTP server or
prints a success-shaped phone URL. It exits nonzero with local-use guidance and
this document. The shortcut does not inspect whether a gateway is configured.
Use `make api` for this Mac. For development, `make ui`/`make dev` keep Vite's
original browser Host when proxying `/api`, so same-origin POSTs remain usable.
Keep Vite on its supported local bind; exposing its dev proxy is not a deployment.

## Explicit POSIX gateway

Use the same selected project/state as the existing local app. Build the frontend
normally, then run the separate command in the foreground:

```bash
zotero-summarizer serve-mobile \
  --project-root /ABSOLUTE/SELECTED/PROJECT \
  --origin https://reader.example-tailnet.ts.net \
  --owner-login owner@example.net
```

Replace placeholders with the approved origin and exact owner's ASCII login.
The origin is HTTPS `.ts.net`, with no credentials, path, query, fragment or
nonstandard port. DNS case and explicit `:443` normalize to the same authority.
Owner matching is exact: no trimming, case folding, wildcard or domain-wide rule.
The configured owner is authorized across their admitted tailnet devices; this is
**not a per-physical-device app permission system**.

Separately, only after approving that device/tailnet policy and checking the
installed gateway's behavior, configure its explicit HTTPS-to-Unix target:

```bash
tailscale serve --https=443 unix:/ABSOLUTE/SELECTED/PROJECT/data/mobile/api.sock
```

The app **never executes this command**, queries accounts, installs a gateway,
changes ACLs, enables Funnel, provisions certificates or opens a TCP listener in
mobile mode. There is no second login, session database, bearer key, API key or
frontend credential. Existing SPA and API/sync clients use relative URLs at the
approved HTTPS origin. Setup/Doctor verification does not establish gateway or
mobile-device readiness.

## Admission and ownership

Mobile admission requires the real Unix transport's unknown peer and exact
configured local socket address, internal HTTP/WS scheme, backend Host `localhost`,
one exact owner login, gateway-derived HTTPS forwarding and the matching external
authority. Funnel, missing/tagged/non-owner identity, duplicates, TCP/unknown
transport, foreign Origin and cross-site browser requests fail before handlers.
Only after admission are scheme/Host rewritten from the configured canonical
policy for redirects. Header values alone do not authenticate a remote caller.

The selected project/data paths must be canonical, user-owned and not writable
by group/others. Trusted root/user-owned system ancestry may be sticky; unsafe
nonsticky writable or foreign-owned ancestry is refused. Inspect OS ACLs and
service privileges separately before deployment; POSIX mode checks are not an ACL
or host-compromise sandbox. Same-UID/root software remains trusted. Symlinked data
or mobile directories are refused, not repaired. Existing directories are never
recursively chmodded. The private mobile directory is `0700`, socket `0600`.
Windows and non-POSIX filesystems without these guarantees are unsupported.

The launcher supplies a prebound socket to Uvicorn instead of its normal `--uds`
creation, which would chmod to `0666`. Only its captured socket device/inode can
be unlinked. Existing/stale sockets, files, symlinks and replacements are preserved
and cause refusal; no foreign listener is killed. Normal/startup-error and graceful
SIGTERM/SIGINT paths close/release the owned socket, including interruptions during
bind or before server startup. Signal capture begins before binding and replays the
interruption only after owned cleanup. SIGKILL may leave residue: inspect its owner explicitly
before any manual cleanup, rather than deleting a possibly live listener.

`ZS_MOBILE_SHUTDOWN_SECONDS` bounds Uvicorn's graceful task wait: default 30,
allowed 1–120, malformed/out-of-range values fail. It does not forcibly terminate
synchronous downstream work, guarantee model/thread teardown or make native writes
atomic. Interrupted exit status is nonzero; its exact signal/exception encoding
can depend on the Python/server execution path.

## Revocation and proof limits

Tailscale owns authenticated device admission, TLS and owner identity. Use its
existing device logout/admin controls. Each new HTTP/WS request is checked; no
immediate revocation propagation or termination of already-open streams is
promised. Stopping the app owns its server/socket, not the Tailscale daemon.
Revocation cannot erase already cached offline bytes. Preserve unsynced human work;
return to the same approved HTTPS origin after explicit reauthorization.

Source references: published Tailscale v1.96.5
[`serve.go`](https://github.com/tailscale/tailscale/blob/88200dbb4515f7396384b8f1c9e0a80566d18834/ipn/ipnlocal/serve.go#L1036-L1088)
clears caller identity and regenerates WhoIs user and TLS/authority headers; Go 1.26.1
[`ReverseProxy`](https://github.com/golang/go/blob/e87b10ea2a2c6c65b80c4374af42b9c02ac9fb20/src/net/http/httputil/reverseproxy.go#L499-L515)
removes caller forwarding headers before Rewrite. Uvicorn 0.46.0
[`server.py`](https://github.com/encode/uvicorn/blob/b224045f5900b7f766743bcb16ba9fc3adea2606/uvicorn/server.py#L125-L166)
uses supplied sockets without the ordinary UDS chmod. All are BSD-licensed;
no upstream code is vendored. These published-source pins are **not installed
binary/build qualification**. HTTP uses the inspected h11 path.

Tests cover actual factory/fixture assets, synthetic admission matrices, real
Uvicorn Unix HTTP/WebSocket with explicitly forged fixture identity headers,
owned listener/signal/restart behavior and actual Vite-to-local POST. They do not
prove real TLS, Tailscale sanitization at runtime, private device policy, physical
iPhone/iPad installation, days-offline durability, revocation or full app/model
shutdown. Those deployment/device checks remain unperformed and separately require
owner approval. This gateway alone does not complete the offline-device feature.
