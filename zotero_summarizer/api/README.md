# api — FastAPI app + HTTP layer

Builds the FastAPI app, mounts the React SPA at `/`, and exposes the JSON API
under `/api/*`. Routes stay *thin*: they validate input and call `services/`.

```
create_app(settings, mobile_access=None)
   ├─ local admission by default / explicit owner + private Unix or trusted-localhost gateway policy
   ├─ include_routes(app)        # routes/__init__.py registers every router
   ├─ mount SPA  (/ + PWA public files -> frontend/dist)
   └─ lifespan: services.lifecycle.startup()  on boot
errors.py  ── APIError -> uniform JSON error body + handlers
```

| file | responsibility |
|---|---|
| `app.py` | `create_app()` factory — wiring, SPA/assets/PWA-public-file mount, exception handlers, lifespan (no import-time app; uvicorn uses `app:create_app` with `factory=True`). The lifespan runs `setup.bootstrap.bootstrap_phase0` (idempotent first-run seed of `goals.yaml` etc., never overwrites) **before** `lifecycle.startup`, which fails fast on a missing config. |
| `errors.py` | `APIError` + the canonical error schema and FastAPI handlers |
| `local_access.py` / `mobile_access.py` | Whole-app local admission or explicit exact-owner/TLS-metadata gateway policy; fixed errors/canonical redirects. Private Unix refuses TCP. Opt-in `trusted_loopback_port` admits only the exact 127.0.0.1 server port and loopback peer, consciously trusting local processes. |
| `routes/` | one module per resource (see routes/README.md) |

The gateway authorizes an owner, not a unique device. Private Unix trusts same-UID/root
software; explicit localhost mode trusts all local host processes. Tailscale rewrites
Unix Host to localhost but preserves the canonical HTTPS Host for TCP; both modes
retain exact login/forwarded authority, origin/fetch-site, duplicate-header and Funnel
refusals. Active-stream revocation and installed proxy/TLS/device acceptance remain
separate. See [remote-access boundary](../../docs/features.md#remote-access).

**Compression:** `GZipMiddleware` (≥1 KB, gzip only; no brotli dependency) compresses
JSON and `/assets` for clients sending `Accept-Encoding: gzip`; `text/event-stream` is never
compressed (the app has no SSE routes today). **First sync:** a fresh browser calls
`/api/sync/pull?since=0` (full snapshot, ~1.9 MB raw); gzip is the only mitigation, there is no incremental first load.

**Boundaries:** may import `services/`, `models`, `errors`. Routes should hold
no business logic — push it into `services/`.

Validation errors expose only field locations and error types; integration errors
use fixed messages so rejected secrets and local paths are never echoed. Partial
frontend builds keep SPA navigation available; missing assets/public files and
both `/api` and unknown `/api/*` requests return 404.

Deep-review status remains a transparent per-item service payload: `error` is
unchanged; additive `diagnostic` exposes only code/stage/recovery and `attempt`
contains redacted hashes/counts. Model prose never determines HTTP/auth status.
There is no API switch for sensitive capture; that consent lives only in the CLI.

Search screening accepts optional `constraints` arrays (`must_include`,
`must_not_include`, `study_types`), each at most ten nonblank 200-character values.
Supplying these fields is user confirmation, not model extraction. Search plan
JSON includes `constraint_origin`, `pending_constraints`, and observed
`retrieval_accounting`; old saved plans remain visibly legacy.

Review diagnostics classify generation, verification and operational source failure from actual boundaries. Source-unavailable ready jobs are not verified reviews; confirmed search types remain unconfirmed until supported source metadata exists.

- Static PWA files served from `frontend/dist` now include `apple-touch-icon.png` (iOS home-screen icon).
