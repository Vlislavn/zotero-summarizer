# api — FastAPI app + HTTP layer

Builds the FastAPI app, mounts the React SPA at `/`, and exposes the JSON API
under `/api/*`. Routes stay *thin*: they validate input and call `services/`.

```
create_app()
   ├─ include_routes(app)        # routes/__init__.py registers every router
   ├─ mount SPA  (/  -> frontend/dist)
   └─ lifespan: services.lifecycle.startup()  on boot
errors.py  ── APIError -> uniform JSON error body + handlers
```

| file | responsibility |
|---|---|
| `app.py` | `create_app()` factory — wiring, SPA mount, exception handlers, lifespan (no import-time app; uvicorn uses `app:create_app` with `factory=True`) |
| `errors.py` | `APIError` + the canonical error schema and FastAPI handlers |
| `routes/` | one module per resource (see routes/README.md) |

**Boundaries:** may import `services/`, `models`, `errors`. Routes should hold
no business logic — push it into `services/`.
