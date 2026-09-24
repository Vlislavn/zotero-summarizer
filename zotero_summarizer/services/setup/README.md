# services/setup — one setup domain, two front-ends

The web wizard chooses Full local, Hosted or No LLM **before** Zotero paths;
ML-only skips the model step. Full-local mode shows compatible profiles with
no API-key field. Doctor's fresh-process asset probe not only enables HF
cache-only flags but denies/records outbound TCP attempts during model loads;
`strict_offline` cannot be Ready if an asset tried the network, even if the
loader raised before returning its inventory. Persisted Doctor success is
invalidated when goals/routing, calibration or `.env` Zotero/PDF paths
change. Enabled RSS source IDs/names/URLs, the effective offline mode and in-app
credential replacement also invalidate Ready. Credential writes serialize
with Doctor checks, rotate an app-owned `data/setup_credential_revision.json`
marker **before** the key changes, and clear any cached browser Ready result;
a concurrent run cannot publish stale success for a new key. A saved path differing from startup Settings demands
restart before checks can pass. Zotero remains diagnostic but optional for
RSS-only setups; an enabled RSS source is still required for Today.

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

An empty `ZOTERO_DATA_DIR` is treated as unset, so path detection falls back to
the per-user Zotero location instead of mistaking the application checkout for
a Zotero data directory.

`light` uses `qwen3:8b` (12 GB memory / 8 GB disk floor); `balanced` uses
`qwen3:30b` (32 GB / 22 GB). `existing` accepts an explicit model and compatible
endpoint. Add runtimes only with install and verification paths.

Doctor state is `data/setup_doctor.json`; stale `running` rows become Needs
action **and clear any earlier Ready flag**, even if the last full pass succeeded.
It is current state, not an audit log. Recovery strings are displayed,
never executed. `--fix` only runs idempotent bootstrap/migrations.
Bootstrap owns migration; Doctor does not repeat it. Path validation snapshots
read the resulting `.env`, including unchanged paths and empty update requests.
Calibration accepts 1–10 papers, checks item paths (including symlinks) remain
inside `Settings.paper_render_dir`, and loads input before contacting a model.
ML-only mode marks model/inference/dry-run checks as intentionally skipped and
keeps classifier/search triage available; it cannot generate the full-text
review required before an app-controlled feed Add. Manual Zotero imports remain
possible, and AI can be enabled later in Settings.

The advisory classifier panel uses the same loaded-gate card as Settings. A
saved but unloaded artifact is not reported as the active classifier; no model
file or run-log parsing is needed for this panel.

The `.env` writer/bootstrap exception exists because paths must resolve before
`Settings.data_dir`. Setup may import models, storage, integrations,
`api.errors`, and services; never API routes. Lower layers never import setup.

Gate asset targets carry the same immutable base/adapter revisions as the
classifier loader. The cheap cache report checks that revision, not merely any
bytes under the repository; an old snapshot cannot satisfy a new pin. Prefetch
uses the actual pinned loader. Snapshot presence remains a cheap inventory
check, while Doctor's fresh-process offline load verifies loadability.
