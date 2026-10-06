# api — FastAPI app + HTTP layer

Builds the FastAPI app, mounts the React SPA at `/`, and exposes the JSON API
under `/api/*`. Routes stay *thin*: they validate input and call `services/`.

```
create_app()
   ├─ include_routes(app)        # routes/__init__.py registers every router
   ├─ mount SPA  (/ + PWA public files -> frontend/dist)
   └─ lifespan: services.lifecycle.startup()  on boot
errors.py  ── APIError -> uniform JSON error body + handlers
```

| file | responsibility |
|---|---|
| `app.py` | `create_app()` factory — wiring, SPA/assets/PWA-public-file mount, exception handlers, lifespan (no import-time app; uvicorn uses `app:create_app` with `factory=True`). The lifespan runs `setup.bootstrap.bootstrap_phase0` (idempotent first-run seed of `goals.yaml` etc., never overwrites) **before** `lifecycle.startup`, which fails fast on a missing config. |
| `errors.py` | `APIError` + the canonical error schema and FastAPI handlers |
| `routes/` | one module per resource (see routes/README.md) |

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
