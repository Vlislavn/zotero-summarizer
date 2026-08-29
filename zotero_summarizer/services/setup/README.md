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
| `status.py` | Cheap state: `configured` means personalized goals plus either an AI credential or an explicit ML-only choice; `ready` also needs Doctor, and reachability only when AI is enabled. |
| `profiles.py` | Resolve hardware-gated Ollama profiles into the existing routing schema; never download. |
| `assets.py` | Shared prefetch targets and fresh-process cache-only load checks. |
| `doctor.py` | Persisted web/CLI checklist with stable IDs, recovery actions, single-flight execution, interrupted-run recovery, redaction, and real inference gating. Recovery distinguishes absent/stopped Ollama; browser readiness checks the actual `patchright` runtime; the RSS probe is read-only so it cannot collide with daemon schema work. |
| `doctor_environment.py` | Host/config/Zotero/database Doctor checks and the shared row contract; split from orchestration so both modules fit the code-size gate. |
| `calibration*.py` | Existing endpoint calibration and its single-flight job. |

`light` uses `qwen3:8b` (12 GB memory / 8 GB disk floor); `balanced` uses
`qwen3:30b` (32 GB / 22 GB). `existing` accepts an explicit model and compatible
endpoint. Add runtimes only with install and verification paths.

Doctor state is `data/setup_doctor.json`; stale `running` rows become Needs
action. It is current state, not an audit log. Recovery strings are displayed,
never executed. `--fix` only runs idempotent bootstrap/migrations.
ML-only mode marks model/inference/dry-run checks as intentionally skipped and
keeps classifier triage available; it can be changed later in Settings.

The `.env` writer/bootstrap exception exists because paths must resolve before
`Settings.data_dir`. Setup may import models, storage, integrations,
`api.errors`, and services; never API routes. Lower layers never import setup.
