# Local deployment boundary (#11 / #19)

The supported launcher is `zotero-summarizer serve` on this machine. It accepts
`127.0.0.1`, `localhost`, `::1`, or another literal loopback address. A remote or
wildcard `--host` fails with an explanation, never silently changes the address.
This does **not** configure authenticated mobile access.

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

## Concrete next remote gate (NOT approved or implemented)

Proposed topology for owner review:

```text
single authorized iPhone/iPad
   -> HTTPS with device-trusted certificate
   -> single-user authenticated proxy
   -> private loopback app transport
   -> existing typed sync API / canonical state
```

Before implementation the owner must approve the identity provider, authentication
and session/logout policy, exact HTTPS origin, proxy isolation and header handling,
who can reach the loopback port, and revocation/recovery. The app would need an
explicit deployment policy tied to that topology; allowing a Host or trusting a
header alone is insufficient. Reject anonymous requests before any app work.
Never expose `/api/sync` separately from the remaining private API. Synthetic ASGI
or desktop browser evidence is not HTTPS, iOS, personal-data or human acceptance.

## Scoped STRIDE decisions

| Threat / affected asset | Current mitigation or remaining gate |
|---|---|
| Spoofing: remote user poses as owner through forwarded headers | Local-only bind and numeric transport-peer check; no header identity trusted. Local-process identity remains an explicit limitation, not user authentication. |
| Tampering: hostile website sends verdict/config writes; DNS rebinding targets loopback | Local Host plus same-origin/Fetch Metadata check before all handlers. Typed mutations and existing validation remain unchanged. HTTPS for remote transit is deferred to the owner gate. |
| Repudiation: local actors share the machine | Existing UUID mutation/transition semantics retained, not upgraded to authenticated attribution or an exactly-once JSONL log. Owner must resolve attribution for remote deployment. |
| Disclosure: exposed API/SPA/docs reveals private library data | The same guard covers reads and writes, static routes and sockets. Errors contain no supplied headers. Local host users remain in the trust boundary. |
| DoS: remote caller triggers models or file work | Refuse before handler work. Existing work limits remain; no new rate-limit policy or protection from local resource abuse is claimed. |
| Elevation: forged identity grants writes using app privileges | No authorization claims are consumed. Remote authentication/authorization and service privilege review are separate unapproved gates. |

Supervisor decision for wave43 authorized loopback hardening only. Product,
infrastructure and independent security reviewers retain remote-risk acceptance;
no proxy or TLS configuration has been activated.

## Surface-simplicity gate

Source consulted: the project's
[Laws-of-UX guidance](ux-review/2026-06-26-triage-workflows/README.md).

| Lens | Action |
|---|---|
| Hick / recognition | Reuse `serve --host`; name supported loopback examples and the unsupported mobile boundary in one actionable error. No competing login/deployment wizard. |
| Cognitive load | A bind failure is explicit and nonzero; no silent fallback or success-shaped remote readiness. Detail belongs here, not another Settings health card. |
| Subtract first / consistency | Keep existing PWA, setup Doctor and sync UI. Defer remote controls until the owner approves their trust model. No visual changes or pixel-acceptance claim. |

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
requests and socket policy rejection. It does not launch or certify TLS/proxies,
models or devices. Retained source/RED/GREEN identities and resource receipts
belong to the wave43 validation artifact, not this design document.
