# services/setup — one setup domain, two front-ends

Web and CLI share profile, validation, and Doctor services. State lives under
`Settings.data_dir`; secrets stay in the keyring or legacy environment.

```text
web /api/setup/* ─┐
                  ├─ bootstrap → profiles → doctor → verified
CLI setup/doctor ─┘                    ├─ per-stage inference
                                      ├─ cache-only ML loads
                                      └─ no-write triage
```

| file | responsibility |
|---|---|
| `bootstrap.py` | Idempotently create absent files and migrate DBs; never overwrite user files. |
| `detect.py` / `env_writer.py` | Find Zotero paths; atomically write validated `PDF_ROOT`/`ZOTERO_DATA_DIR`. |
| `validate.py` | Validate drafts and optionally reuse the provider probe; write nothing. |
| `status.py` | Cheap state: `configured` means personalized goals plus credential; `ready` also needs reachability and a successful Doctor run. |
| `profiles.py` | Resolve hardware-gated Ollama profiles into the existing routing schema; never download. |
| `assets.py` | Shared prefetch targets and fresh-process cache-only load checks. |
| `doctor.py` | Persisted web/CLI checklist with stable IDs, recovery actions, single-flight execution, interrupted-run recovery, redaction, and real inference gating. |
| `calibration*.py` | Existing endpoint calibration and its single-flight job. |

`light` uses `qwen3:8b` (12 GB memory / 8 GB disk floor); `balanced` uses
`qwen3:30b` (32 GB / 22 GB). `existing` accepts an explicit model and compatible
endpoint. Add runtimes only with install and verification paths.

Doctor state is `data/setup_doctor.json`; stale `running` rows become Needs
action. It is current state, not an audit log. Recovery strings are displayed,
never executed. `--fix` only runs idempotent bootstrap/migrations.

The `.env` writer/bootstrap exception exists because paths must resolve before
`Settings.data_dir`. Setup may import models, storage, integrations,
`api.errors`, and services; never API routes. Lower layers never import setup.
