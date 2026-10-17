# Local deployment boundary (#11 / #19)

The supported launcher is `zotero-summarizer serve` on this machine. It accepts
`127.0.0.1`, `localhost`, `::1`, or another literal loopback address. A remote or
wildcard `--host` fails with an explanation, never silently changes the address.
This does **not** configure authenticated mobile access.

The legacy `make iphone` shortcut fails immediately with this limitation, a link
to this document and the supported Mac command: `make api`, then open
`http://127.0.0.1:8000`. It does not discover a LAN address, print a phone URL,
invoke the server or silently substitute loopback for mobile access.

The app factory guards every HTTP and WebSocket route, including the SPA, static
files, docs and sync. Before route work it requires:

- a numeric loopback ASGI client address (including IPv4-mapped loopback);
- one well-formed local Host authority;
- an absent Origin (ordinary CLI/MCP clients), or one matching the request's
  scheme, hostname and effective port;
- absent Fetch Metadata, `same-origin`, or `none`, not a different-site browser
  request. Duplicate authority/origin/site headers fail closed.

HTTP rejection is a fixed `403 local_access_only` JSON response; WebSocket
rejection is policy close `1008`. Request paths, headers and identities are not
reflected in the error. There is no CORS allowlist, key, login surface or bypass.

## Trust assumptions and unsupported exposure

The CLI disables Uvicorn proxy-header processing. The guard uses the transport's
ASGI peer and scheme; it does not read `Forwarded`, `X-Forwarded-*` or user identity
headers. A different ASGI runner must preserve those transport facts. Middleware
that rewrites the peer can invalidate the boundary. A loopback proxy that rewrites
Host/Origin or hides remote callers is **not** an approved deployment. Loopback
checks are not authentication: other processes and users on the same host remain
trusted. No protection against compromised local software is claimed.

The PWA/IndexedDB/outbox continue to work on the supported local origin; no sync
semantics, receipts, queue, schema, models or storage policy change here. The app
may be cached while the server is off, but real iPhone/iPad installation, restart,
days-offline durability and reconciliation still need device evidence. This
hardening does not satisfy remote mobile access or close #19.

## Local verification progress

The shared setup/Settings Doctor panel observes an already running verification
after reload using conditional GET status polling. It disables duplicate Verify
and retry actions while either the local request or server run is active; terminal
status or a read error stops polling. Mounting the panel never starts inference.
This display correction grants no remote access, model download or device trust,
and does not change backend deadlines or readiness criteria.

## Explicit authenticated gateway (source implemented; activation not performed)

```text
single authorized iPhone/iPad / Tailscale identity
   -> Tailscale Serve HTTPS on the approved .ts.net origin
   -> private 0600 Unix socket in Settings.data_dir/mobile (0700)
   -> exact owner + gateway/origin checks before every route
   -> existing React PWA / typed sync API / canonical state
```

Default `serve` remains loopback-only. The separate opt-in command is:

```bash
zotero-summarizer serve-mobile \
  --origin https://reader.example-tailnet.ts.net --owner-login owner@example.net
# Separately, after approving the tailnet/device policy and actual origin:
tailscale serve --https=443 unix:/ABSOLUTE/SELECTED/data/mobile/api.sock
```

Replace the generic origin/login/path with the selected owner's deployment values.
The app never runs Tailscale commands, queries accounts, changes ACLs, enables
Funnel or opens a TCP listener. The launcher uses a prebound private socket, not
Uvicorn's `--uds` socket creation, which otherwise chmods the socket to 0666.
An existing socket/path is never removed on startup; shut down its owner first.
Cleanup only unlinks the same socket inode created by this invocation. Its release
runs inside Uvicorn's signal context before captured SIGTERM/SIGINT is re-raised;
normal/error cleanup remains in the outer owner. SIGKILL can leave a stale path,
which is not silently deleted on the next startup.

Tailscale owns device authentication, trusted TLS and tailnet admission. The app
requires its exact configured owner login, exact external HTTPS origin and actual
Unix-socket transport/address; incoming identity headers on TCP/unknown transports
are never trusted. Funnel, anonymous/tagged identities, duplicate security headers
and cross-site/foreign-origin browser requests are rejected before API, SPA, docs,
assets or WebSocket work. Uvicorn forwarded-header processing stays disabled.
Only after validation does the app use its configured HTTPS authority for redirects.
Same-host same-UID/root processes remain trusted, as for the existing local app;
this is not a claim that a header authenticates arbitrary local software.

There is no second app session/cookie/login system. Logout/revocation is through
Tailscale on the device or its existing device-admin controls; future online app
requests then lack the authorized transport identity. Offline cached bytes cannot
be remotely erased: already cached cards and unsynced human work remain on that
trusted device. Do not silently clear pending edits. Recovery requires reauthorizing
an approved device and returning to the same HTTPS origin. Real logout/revocation,
TLS reachability, app installation, server-off restart and ordered reconciliation
still require explicit deployment/device evidence. Source/unit contracts are not
such evidence; no real tailnet or iPhone/iPad activation has been performed here.

Primary sources: installed Tailscale 1.96.5 `serve --help` explicitly supports Unix
sockets; published v1.96.5 `ipn/ipnlocal/serve.go` removes caller identity before
WhoIs-derived user headers, rejects identity for Funnel/tagged traffic, preserves
external authority in forwarded headers and uses Host `localhost` for Unix proxying.
Go 1.26.1 `net/http/httputil/reverseproxy.go` removes incoming forwarded headers
before its Rewrite callback, so a plaintext caller cannot supply a surviving
`X-Forwarded-Proto: https`. These sources are BSD-licensed; no code is vendored.
The installed build's reported commit was not available at the upstream raw URL;
the matching published version tag is reference evidence, not binary qualification.

## Scoped STRIDE decisions

| Threat / affected asset | Current mitigation or remaining gate |
|---|---|
| Spoofing: remote user poses as owner through forwarded headers | Local-only bind and numeric transport-peer check; no header identity trusted. Local-process identity remains an explicit limitation, not user authentication. |
| Tampering: hostile website sends verdict/config writes; DNS rebinding targets loopback | Local Host plus same-origin/Fetch Metadata check before all handlers. Typed mutations and existing validation remain unchanged. HTTPS for remote transit is deferred to the owner gate. |
| Repudiation: local actors share the machine | Existing UUID mutation/transition semantics retained, not upgraded to authenticated attribution or an exactly-once JSONL log. Owner must resolve attribution for remote deployment. |
| Disclosure: exposed API/SPA/docs reveals private library data | The same guard covers reads and writes, static routes and sockets. Errors contain no supplied headers. Local host users remain in the trust boundary. |
| DoS: remote caller triggers models or file work | Refuse before handler work. Existing work limits remain; no new rate-limit policy or protection from local resource abuse is claimed. |
| Elevation: forged identity grants writes using app privileges | No authorization claims are consumed. Remote authentication/authorization and service privilege review are separate unapproved gates. |

Wave43 authorized loopback hardening only. The later parent-owned gateway source
chooses Tailscale Serve plus an owner-only Unix transport instead of custom auth.
Independent security review, actual deployment and final device acceptance remain
separate gates; no proxy/TLS configuration or account was activated.

## Surface-simplicity gate

Source consulted: the project's
[Laws-of-UX guidance](ux-review/2026-06-26-triage-workflows/README.md).

| Lens | Action |
|---|---|
| Hick / recognition | Reuse `serve --host`; name supported loopback examples and the unsupported mobile boundary in one actionable error. No competing login/deployment wizard. |
| Cognitive load | A bind failure is explicit and nonzero; no silent fallback or success-shaped remote readiness. Detail belongs here, not another Settings health card. |
| Subtract first / consistency | Remove the obsolete `make iphone` LAN/Tailscale recipe and premature phone URL; keep a direct failure with the supported Mac command and this document. Keep existing PWA, setup Doctor and sync UI; defer remote controls until the owner approves their trust model. No new bypass or visual changes. |

## Primary-source grounding and checks

Opened installed sources (read-only; not vendored):

- Uvicorn `protocols/utils.py:get_remote_addr` and
  `protocols/http/h11_impl.py:connection_made`: scope peer comes from socket
  `getpeername`; scheme comes from the transport's SSL context.
- Uvicorn `middleware/proxy_headers.py:ProxyHeadersMiddleware` and
  `config.py:Config.load`: trusted forwarded headers can replace client/scheme;
  disabling that wrapper is necessary for the supported launcher.
- Starlette `testclient.py:TestClient` / `_TestClientTransport`: public `client=`
  and `base_url=` supply synthetic peer/authority. No production test-client
  exception is warranted.
- Starlette `middleware/trustedhost.py`: separates Host checks from handler work;
  its colon-splitting host implementation is not reused for bracketed IPv6.
- Codex `core/src/tools/orchestrator.rs`: approval preparation is separate from
  approval. This supports keeping a proposed remote topology unapproved, not a
  claim that Codex implements this transport policy.

`tests/test_local_access_boundary.py` exercises the actual factory with synthetic
loopback TestClient transport and no lifespan startup, plus a mocked launcher.
It asserts successful local calls and rejected remote/unknown peers, forged
headers, hostile/malformed/duplicate authorities, different origins, cross-site
requests and socket policy rejection. `tests/test_make_launchers.py` executes the
real Makefile in isolated projects with stub executables, with and without a
venv. It verifies that `iphone` fails before address discovery or server launch,
while `api` preserves the loopback command. These checks do not launch or certify
TLS/proxies, models or devices. The earlier boundary-hardening source/RED/GREEN
identities and resource receipts belong to the wave43 validation artifact,
not this design document.
