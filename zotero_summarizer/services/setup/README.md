# services/setup — one setup domain, two front-ends

The web wizard and CLI delegate to the same profile, validation and doctor
services. Setup state is persisted under `Settings.data_dir`; provider secrets
remain in the OS keyring or legacy environment variables.

```text
web /api/setup/* ─┐
                  ├─ bootstrap → profiles → doctor → Ready
CLI setup/doctor ─┘       │           ├─ real per-stage inference
                          │           ├─ cache-only ML loads
                          │           └─ no-write abstract triage
                          └─ existing llm_routing config
```

| file | responsibility |
|---|---|
| `bootstrap.py` | Idempotently create absent config/env files and migrate both DBs on every startup (including restored/partial state); never overwrite user files. |
| `detect.py` | Read-only platform Zotero-directory candidates. |
| `env_writer.py` | Atomically write only `PDF_ROOT` and `ZOTERO_DATA_DIR`, after path validation. |
| `validate.py` | Validate a draft, reject bootstrap example goals, and optionally reuse the real provider probe; write nothing. |
| `status.py` | Cheap setup gate: valid goals, credential presence, model reachability, and a successful persisted Doctor run; credential presence is a bool, never a value. |
| `profiles.py` | Versioned Light/Balanced/Existing local recommendations, hardware compatibility, and resolution into the existing provider/stage schema. It never downloads a model; fixed profiles return an explicit `ollama pull` command. |
| `assets.py` | One target list for prefetch, cache reporting, and a fresh-process cache-only load check. |
| `doctor.py` | Persisted structured checklist shared by `POST /api/setup/doctor` and `zotero-summarizer doctor`; stable check IDs, five statuses, recovery actions, single-flight execution, interrupted-run recovery, redacted details, and mode readiness gated by real inference. Best-effort safe `--fix` is limited to bootstrap/migrations. |
| `calibration.py` / `calibration_job.py` | Existing measured endpoint calibration and its single-flight background wrapper. |

Local profiles are deliberately Ollama-only today. `light` uses `qwen3:8b`
(5.2 GB download; 12 GB memory / 8 GB free-disk floor); `balanced` uses
`qwen3:30b` (19 GB; 32 GB / 22 GB floor). `existing` accepts an explicitly
named model and OpenAI-compatible endpoint. Add another runtime only after it
has a supported install and real verification path.

Doctor state lives at `data/setup_doctor.json`; stale `running` rows resume as
`Needs action`. It is current setup state, not an audit log.

Security: APIs never return credential values; doctor redacts resolved secrets,
accepts only allowlisted retries, and never executes recovery strings. Downloads
start only through the displayed user-run command. `validate.py`/`status.py` are
read-only; doctor `--fix` only runs idempotent bootstrap/migrations.

The `.env` path writer/bootstrap are the sanctioned state-location exception:
these values must exist before `Settings` can resolve `data/`. All doctor state
and calibration remain under `data/`.

**Boundaries:** setup may import models, storage, integrations, `api.errors`,
and other services; it never imports API routes. Storage/integrations never
import setup.
