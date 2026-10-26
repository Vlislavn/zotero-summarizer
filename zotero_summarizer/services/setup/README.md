# services/setup — one setup domain, two front-ends

## Trustworthy verification boundary

`load_admission.py` separates hardware safety from listing/configuration and
profile advice. No current adapter produces an authoritative effective LLM
ResourceEnvelope (context, placement, memory domain), so all local LLM inference
and dry-run callbacks refuse UNKNOWN before constructing a client; this includes
loopback native Anthropic. Optional byte-bound operator policy cannot replace
runtime evidence; the two-argument provider/model admission remains canonical.
Hosted/no-LLM and nonloading metadata remain available. The existing
bounded cache-only ML asset subprocess is separate from LLM admission.
The shared calibration orchestrator opts into this admission for both local
client constructions, including cached Tier-1 runs; HTTP jobs surface refusal
as an error, not Ready. Saved No-LLM refuses calibration with `llm_disabled` (409)
before retained-paper reads, progress, credentials, clients or Tier-1 writes;
HTTP jobs become error, never Ready. Enabled hosted calibration retains ordinary
transport behavior. CLI Doctor inventories custom remote identities from its
selected saved configuration without initializing the full application; unsaved
drafts still cannot send server-held credentials to arbitrary endpoints.

`load_attempt.py` uses setup's daemon-worker pattern with a bounded waiter and
callback-owned exclusion. Cancellation/timeout stops later requests, never claims
remote termination. Only the callback releases its lock; separate request records
in the existing Doctor file retain unresolved local transport-entry HOLD or
unobserved-request uncertainty across callback return, configuration and successor
publication. An exact original terminal observation can reconcile only its request,
never resume cancellation or publish Ready. This is not a cross-process kill/lease.
Doctor persists each completed check and a version-bound final result; legacy
success must be reverified. Inventory uses the same callback exclusion and checks
cancellation before each endpoint. Doctor inference stays in that callback rather
than an executor that can detach an unfinished request on shutdown. New runs reset
their current callback receipt; predecessor request fences remain separately
retained. Callback results use only their own request slice, not earlier checks.
Expected identity, owned native callback, actual HTTPX pre-send entry and terminal
model/reason are distinct evidence. Text, callback return and client close cannot
establish observed completion; pre-send entry is not remote acceptance. Unknown
instrumentation cannot be relabelled as positive no-send proof. Safety receipts may
be written before verification, while draft configuration files remain unchanged. Local-inference mode requires
both the inference and no-write dry-run checks; successful inference alone cannot
override a failed dry-run. Status reads recompute modes from the authoritative
checklist without rewriting state or invoking checks; stale saved mode labels are
not authority. Asset readiness remains a separate offline prerequisite. Active cancellation IDs
remain available after configuration changes. `POST /api/setup/doctor/cancel` accepts the current
`run_id`, so a stale cancel cannot affect a successor. Web and CLI share this
boundary; issue #11's hardware-safe Full-local acceptance remains unfulfilled.

## Optional load approval files

```text
Settings file + approved SHA -> bounded/schema read -> exact provider/model
fresh executing runtime -> matching envelope + budget/reserve comparison
no authoritative producer -> UNKNOWN -> no credentials/client/request
```

- Settings reads `ZS_SETUP_LOAD_APPROVAL_POLICY_PATH` and `ZS_SETUP_LOAD_APPROVED_POLICY_SHA256`. Both default absent. Relative paths are below `Settings.data_dir`; absolute paths must remain below it. Files and approvals are never created automatically.
- `_load_policy.py` permits only nonempty regular files up to `MAX_POLICY_BYTES` (65,536). Directory-relative no-follow opens reject symlinks, traversal, FIFOs and oversized files before reading; descriptor revisions refuse changes observed while opening/reading. This is a byte snapshot, not a reservation or filesystem lock; reads never write.
- UTF-8 JSON uses the closed `SetupLoadApprovalPolicy` schema: `schema_version` is integer 1; `approval_reference` is a nonempty string up to 256 characters. Duplicate keys, nonfinite numbers, coercions and unknown fields refuse as `policy_configuration_error`.
- `identity` contains only `provider` (the exact JSON-mode `ProviderConfig` dictionary) and `model` (nonempty string, max 1024). Both must exactly match the currently resolved pair, including endpoint and provider options. No credentials are read for this check.
- `effective_envelope` contains only positive integral `context_tokens`, nonempty `placement` and `memory_domain` strings (max 256 each). These are exact runtime identifiers, not inferred hardware claims; no supported placement/domain is invented by the parser.
- `peak_working_set_bytes` is a positive integer; `reserved_system_bytes` is a nonnegative integer. Booleans and numeric strings are invalid. Fresh available bytes must cover their exact sum; equality admits only the policy condition, not physical safety.
- Present files are parsed before approval checking. Missing approval is `policy_unbound`; a different exact lowercase SHA256 of the file bytes is `policy_binding_mismatch`. Malformed hash/path/file is ERROR, not absent policy or consent.
- `_observe_executing_runtime(provider, model)` is a private synchronous observation seam, not a public facts override. Future integration must return fresh `identity`, `effective_envelope` and `available_bytes` from that executing runtime's memory domain on each admission.
- No authoritative producer is integrated: production returns no envelope and remains `effective_envelope_unknown`, even with valid approval. None availability with a known envelope is `telemetry_unavailable`; invalid/failed telemetry is ERROR. Inventory/free host RAM cannot substitute.
- Envelope and runtime identity mismatches refuse separately from policy identity mismatch. Every non-admitted local status refuses before credentials/client construction. Synthetic positive tests certify machine wiring only; `hardware_safe` stays unknown.
- Doctor's existing configuration signature includes policy bytes, configured path/hash and admission revision, never volatile memory samples. Changing approved bytes (even same-length), approval or revision invalidates old Ready; status reads do not approve or rewrite files.

The shared provider predicate includes all literal 127/8 and IPv4-mapped IPv6
loopbacks, numeric IPv4 shorthand and the reserved `.localhost` namespace,
with no DNS lookup. Both probe and explicit verification factory paths
refuse before credentials/adapters. Verification revision v4 invalidates pre-policy
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
a concurrent run cannot publish stale success for a new key. Credential replacement
also invalidates the shared stage-client cache, including if storage raises after a
partial write, so an old-key client or in-flight builder cannot repopulate it. A saved path differing from startup Settings demands
restart before checks can pass. Zotero remains diagnostic but optional for
RSS-only setups; an enabled RSS source is still required for Today. The read-only
RSS check supplies typed `open_sources_settings` recovery; web navigation opens
existing RSS controls without running verification or refreshing sources.

The package façade resolves its existing exported functions lazily to the same
defining objects. Importing lightweight deployment/path validation does not import
status, Zotero or model services; actual setup/Doctor calls retain their behavior.

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
| `deployment.py` | Shared numeric/mapped-loopback classification and refusal for the ordinary local launcher and inbound API guard; no gateway discovery or authentication inference. |
| `bootstrap.py` | Idempotently create absent files and migrate DBs; never overwrite user files. |
| `detect.py` / `env_writer.py` | Find Zotero paths; atomically write validated `PDF_ROOT`/`ZOTERO_DATA_DIR`. |
| `validate.py` | Validate drafts and optionally reuse the provider probe; configuration stays unchanged, while explicit inference may write request-ownership safety receipts. |
| `status.py` | Cheap state: `configured` means personalized goals plus either an AI credential or an explicit ML-only choice; `ready` also needs Doctor, and reachability only when AI is enabled. |
| `profiles.py` | Resolve hardware-gated Ollama profiles into the existing routing schema; never download. |
| `assets.py` | Shared prefetch targets and fresh-process cache-only load checks. |
| `doctor_requests.py` / `doctor_dry_run.py` | Existing Doctor-file ownership persistence and real no-Zotero-write refine/repair/triage pipeline; no separate store or generic runner. |
| `doctor.py` | Persisted web/CLI checklist with stable IDs, recovery actions, single-flight execution, interrupted-run recovery, redaction, and real inference gating. Recovery distinguishes absent/stopped Ollama; browser readiness checks the actual `patchright` runtime; the RSS probe is read-only so it cannot collide with daemon schema work. |
| `doctor_environment.py` | Host/config/Zotero/database Doctor checks, saved-path/restart checks and the shared row contract; split from orchestration so both modules fit the code-size gate. |
| `calibration*.py` | Existing endpoint calibration and its single-flight job. |

An empty `ZOTERO_DATA_DIR` is treated as unset, so path detection falls back to
the per-user Zotero location instead of mistaking the application checkout for
a Zotero data directory.

`light` uses `qwen3:8b` (12 GB memory / 8 GB disk floor); `balanced` uses
`qwen3:30b` (32 GB / 22 GB). `existing` accepts an explicit model and compatible
endpoint. Catalog eligibility checks only static floors: known models say those
floors are met, while an Existing model keeps runtime compatibility/capacity
explicitly unverified. Neither message authorizes inference or Doctor Ready.
Add runtimes only with install and verification paths.

Doctor state is `data/setup_doctor.json`; stale `running` rows become Needs
action **and clear any earlier Ready flag**, even if the last full pass succeeded.
It is current state, not an audit log. Recovery strings are displayed,
never executed. Missing Ollama retains the Homebrew command only on macOS;
Windows, Linux and unknown platforms receive the official platform download URL,
without a guessed shell installer or runtime inventory call. `--fix` only runs idempotent bootstrap/migrations.
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

Model prefetch requires the shared online policy before changing offline flags or
reading configuration/loading assets. Direct service and CLI calls refuse strict
offline without clearing those flags; `prefetch-models --check` remains a separate
nonloading inventory operation. Explicit online prefetch keeps its existing path.

Gate asset targets carry the same immutable base/adapter revisions as the
classifier loader. The cheap cache report checks that revision, not merely any
bytes under the repository; an old snapshot cannot satisfy a new pin. Prefetch
uses the actual pinned loader. Snapshot presence remains a cheap inventory
check, while Doctor's fresh-process offline load verifies loadability.
