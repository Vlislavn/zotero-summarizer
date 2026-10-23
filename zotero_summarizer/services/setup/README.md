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

The supported launcher refuses remote/wildcard binds and ignores forwarded
identity/IP claims. The app factory guards HTTP/SPA/socket access before handlers;
this does not imply authenticated mobile availability or Doctor readiness.
See the [local deployment boundary](../../../docs/local-deployment-boundary.md)
and its separate owner-approved remote-design gate.

Web and CLI share profile, validation, and Doctor services. State lives under
`Settings.data_dir`; secrets stay in the keyring or legacy environment.

Local setup admission runs inside each unique callback before construction,
using one captured resolved identity. Missing policy/effective-envelope evidence
is UNKNOWN; malformed policy/read failures are errors. Configuration, inventory
and separately consented downloads stay independent.

Guarded rows use the locality-neutral header “LLM verification needs action”:
reason codes also describe hosted timeout/cancellation, not only local admission.
Details, attempted/ownership receipts and navigation-only recovery stay unchanged;
a hosted abandonment does not acquire local HOLD.

HOLD reconciliation validates current stage resolution as well as parsing the
configuration. An explicitly unsupported capability declaration keeps inference
and dry-run rows at Needs action in both Doctor execution and status reads; it
cannot escape reconciliation or clear the unresolved-operation HOLD. Unexpected
programmer exceptions remain loud. No credential or inference work is retried.

Waiters never release the callback's nonblocking lock. Timeout/cancel aborts
remaining checks; unproven request/retry finality leaves sticky same-process HOLD
after callback exit. Explicit local setup clients now reserve each text/schema
request and observe matching unique inline starts plus terminal `stop`/`length`.
Only when every expected request terminated, with SDK retries/cache and HTTP
redirects disabled, can the callback avoid a false finality HOLD. `length` still
fails output/readiness. Missing, orphan, duplicate or unfinished events fail
closed; abandonment wins under the existing lock and late completion never clears
sticky HOLD. Client-owned HTTP connections close before callback ownership ends;
this proves neither daemon-wide idleness nor model unloading. Ordinary/hosted
clients retain their prior settings. No reset, resident admission bypass,
cross-process exclusion or restart completion guarantee is provided.

Admission is unchanged: no production effective envelope is observed, so local
Full-local completion remains blocked by UNKNOWN. Terminal-request receipts or
synthetic admission tests are not hardware/capacity evidence.

```text
web /api/setup/* ─┐
                  ├─ bootstrap → profiles → doctor → verified
CLI setup/doctor ─┘                    ├─ per-stage inference
                                      ├─ cache-only ML loads
                                      └─ no-write triage
```

| file | responsibility |
|---|---|
| `deployment.py` | Shared local-only bind/host/address predicates; IPv4-mapped loopback is explicit. No remote auth/session/TLS policy or readiness claim. |
| `bootstrap.py` | Idempotently create absent files and migrate DBs; never overwrite user files. |
| `detect.py` / `env_writer.py` | Find Zotero paths; atomically write validated `PDF_ROOT`/`ZOTERO_DATA_DIR`. |
| `validate.py` | Validate drafts and optionally reuse the provider probe; write nothing. |
| `status.py` | Cheap state: `configured` means personalized goals plus either an AI credential or an explicit ML-only choice; `ready` also needs Doctor, and reachability only when AI is enabled. |
| `profiles.py` | Static total-RAM/disk compatibility into existing routing; neither admission nor resident health. Never download. |
| `load_admission.py` | Strict optional policy integrity/identity/envelope boundary; fresh facts, no production numeric defaults. |
| `load_attempt.py` | Callback-owned setup lock, submission receipts, synchronized abandonment and sticky finality HOLD. |
| `assets.py` | Shared prefetch targets and fresh-process cache-only load checks. |
| `doctor_dry_run.py` | Existing no-write refine/repair/triage callback and scoped client cleanup; re-exported as `doctor._dry_run`. |
| `doctor.py` | Persisted web/CLI checklist with stable IDs, recovery actions, single-flight execution, interrupted-run recovery, redaction, and real inference gating. Recovery distinguishes absent/stopped Ollama and scopes start/pull/refresh guidance to the failing provider's existing runtime identity, never another local provider in a mixed routing configuration. Browser readiness checks the actual `patchright` runtime; the RSS probe is read-only so it cannot collide with daemon schema work. |
| `doctor_environment.py` | Host/config/Zotero/database Doctor checks and the shared row contract; split from orchestration so both modules fit the code-size gate. |
| `calibration*.py` | Existing endpoint calibration and its single-flight job. |

An empty `ZOTERO_DATA_DIR` is treated as unset, so path detection falls back to
the per-user Zotero location instead of mistaking the application checkout for
a Zotero data directory.

`light` uses `qwen3:8b` (12 GB memory / 8 GB disk floor); `balanced` uses
`qwen3:30b` (32 GB / 22 GB). `existing` accepts an explicit model and compatible
endpoint. Add runtimes only with install and verification paths.

Doctor recovery is typed at the shared row boundary: failed profile/runtime,
research-goals, Zotero and RSS checks name the existing Settings control to open.
Both frontends retain actual failure details and explicit commands; opening a
control neither repairs nor reruns the check. Command-based repair remains a
manual action followed by scoped retry. The profile detail lists the configured
provider/models and distinguishes static hardware floors from load verification.

Doctor state is `data/setup_doctor.json`; stale `running` rows become Needs
action **and clear any earlier Ready flag**, even if the last full pass succeeded.
It is current state, not an audit log. Recovery commands are displayed, never
executed; typed `open_ai_settings` only navigates to existing AI controls.
`--fix` only runs idempotent bootstrap/migrations.
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
