# services/setup — one setup domain, two front-ends

## Trustworthy verification boundary

`load_admission.py` separates hardware safety from listing/configuration and
profile advice. No current adapter produces an authoritative effective LLM
ResourceEnvelope (context, placement, memory domain), so all local LLM inference
and dry-run callbacks refuse UNKNOWN before constructing a client; this includes
loopback native Anthropic. No operator policy/synthetic numeric-success API is
introduced. Hosted/no-LLM and nonloading metadata remain available. The existing
bounded cache-only ML asset subprocess is separate from LLM admission.
The shared calibration orchestrator opts into this admission for both local
client constructions, including cached Tier-1 runs; HTTP jobs surface refusal
as an error, not Ready. Hosted calibration retains ordinary transport behavior.

`load_attempt.py` uses setup's daemon-worker pattern with a bounded waiter and
callback-owned exclusion. Cancellation/timeout stops later requests, never claims
remote termination, and retains an unresolved receipt/HOLD. Only the callback can
release its lock; HOLD is diagnostic, not a cross-process runtime kill/lease.
Doctor persists each completed check and a version-bound final result; legacy
success must be reverified. Inventory uses the same callback exclusion and checks
cancellation before each endpoint. Doctor inference stays in that callback rather
than an executor that can detach an unfinished request on shutdown. New runs reset
their receipt; interrupted ownership stays unresolved. Active cancellation IDs
remain available after configuration changes. `POST /api/setup/doctor/cancel` accepts the current
`run_id`, so a stale cancel cannot affect a successor. Web and CLI share this
boundary; issue #11's hardware-safe Full-local acceptance remains unfulfilled.

The shared provider predicate includes all literal 127/8 and IPv4-mapped IPv6
loopbacks, numeric IPv4 shorthand and the reserved `.localhost` namespace,
with no DNS lookup. Both probe and explicit verification factory paths
refuse before credentials/adapters. Verification revision v3 invalidates pre-fix
Ready. Inventory recovery uses the failing provider/model/endpoint, not any sibling:
Ollama suggestions apply to that local OpenAI-compatible endpoint on port 11434;
other runtimes open AI Settings and choose a served model. Model-error prose cannot
select a runtime or produce a pull command; actual inventory recovery is retained.


The web wizard chooses Full local, Hosted or No LLM **before** Zotero paths;
ML-only skips the model step. Full-local mode shows compatible profiles with
no API-key field. Doctor's fresh-process asset probe not only enables HF
cache-only flags but denies/records outbound TCP attempts during model loads;
`strict_offline` cannot be Ready if an asset tried the network, even if the
loader raised before returning its inventory. Persisted Doctor success is
invalidated when goals/routing, calibration or `.env` Zotero/PDF paths
change. Enabled RSS source IDs/names/URLs, the effective offline mode and in-app
credential replacement also invalidate Ready. Effective hosted credential values
and sources contribute only to the opaque signature, so external keyring/env
replacement also invalidates success; local admission still precedes credential
resolution. An empty retry selection rechecks all checks. Credential writes serialize
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
| `doctor_environment.py` | Host/config/Zotero/database Doctor checks, saved-path/restart checks and the shared row contract; split from orchestration so both modules fit the code-size gate. |
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
