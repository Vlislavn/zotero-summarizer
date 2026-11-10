# services/setup — one setup domain, two front-ends

## Connection task integration

Ordinary maintenance is owned by `services/llm/connections`, not repeated Setup.
Setup stays skippable and embeds the canonical ConnectionTask instead of its former
in-place provider/route DTO editor. Profile suggestions only seed that task's draft;
they do not mutate old endpoints/options, homogenize stages or drop explicit no-auth.
Keyless endpoint admission is independent of wizard mode. Missing/invalid config
returns a terminal repair/retry/Skip view, not a permanent Preparing screen.
Explicit no-auth status does not look up a key or claim key presence. First-task
bootstrap compiles a complete config from the explicit real provider/model, not an
invented default; an existing invalid file requires full-config repair instead of
overwrite. Credential replacement retains Doctor's lock/invalidation, now inside
the shared configuration and existing Telegram-operation locks. Doctor admission
is nonwaiting and precedes those locks, so a running check cannot hold unrelated
publication hostage through a queued credential edit. Shared slots need
the connection task's revision-bound consumer preview; legacy unpreviewed shared
writes refuse. Refusing during Telegram work prevents its captured config from
later resolving a newly replaced shared key. Other already-started calls and
external keyring/env edits remain outside this temporal guarantee. Legacy Setup
credential requests may bind an expected revision and receive the exact revision
captured inside the write lock; this does not bypass shared-consumer confirmation.
The ordinary connection task separately tests the saved default or explicitly
selected saved task models through the same bounded SetupAttempt/probe/load/
request-inventory owner; there is no new runner or implicit all-stage reroute. It displays a response or a specific
refusal/UNKNOWN with direct repair. Local UNKNOWN still refuses before construction.
Acknowledged connection-only saves advance Setup only from a matching originating
revision; research/access drafts are rebased through the existing owner. A task save
cannot stamp current permission on a different stale Setup draft.
Resumed browser drafts retain their original config revision instead of attaching
a newly read revision to old routing. Old unversioned drafts require explicit
saved-settings reload; uncertain full-config ACKs do not advance Setup.

## Trustworthy verification boundary

Refusal texts name the numbers and the next step, not internal reason codes (e.g. the local-test refusal shows free RAM/swap, not `memory_insufficient`).

`load_admission.py` separates hardware safety from listing/configuration and
profile advice. Ordinary/external local daemons have no authoritative effective
LLM ResourceEnvelope and refuse UNKNOWN before client construction, including
loopback native Anthropic. An explicitly requested `_local_runtime` scope may bind
one finite-guarded isolated Ollama to the current SetupAttempt. Its owned listener,
runner and native resident digest/context establish execution evidence; CPU/host or
native Metal on Apple Silicon establishes the corresponding memory domain.
Missing facts still refuse; free RAM or inventory alone never admits. Optional byte-bound operator policy cannot replace
runtime evidence; the two-argument provider/model admission remains canonical.
Hosted/no-LLM and nonloading metadata remain available. The existing
bounded cache-only ML asset subprocess is separate from LLM admission.
The shared calibration orchestrator opts into this admission for both local
client constructions, including cached Tier-1 runs; HTTP jobs surface refusal
as an error, not Ready. Saved No-LLM refuses calibration with `llm_disabled` (409)
before retained-paper reads, progress, credentials, clients or Tier-1 writes;
HTTP jobs become error, never Ready. Enabled hosted calibration retains ordinary
transport behavior. CLI Doctor reads its selected saved configuration without full startup, but
secret-bearing custom metadata/verification also needs a current exact task receipt.
Saved membership or an explicit Doctor action alone is not consent; unapproved or
expired permission reports needs-approval with direct connection recovery before
lookup, not endpoint/model failure. Declared no-auth/local non-secret/preset paths
remain admitted. Unknown custom auth presence stays unknown rather than causing
mandatory re-onboarding; saved configuration still is not operational readiness.

`load_attempt.py` uses setup's daemon-worker pattern with a bounded waiter and
callback-owned exclusion. Its optional pre-request admission hook reuses
`require_active` before credential lookup and native send; the saved-model task
binds revisions there rather than holding configuration edits through inference.
Typed failed probe observations survive independently of incomplete inventory;
operational claims still require successful native evidence. Cancellation/timeout
stops later requests, never claims remote termination. Only the callback releases its lock; separate request records
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
- Outside an explicit owned-runtime attempt, production returns no envelope and remains `effective_envelope_unknown`, even with valid approval. The scoped producer rechecks guardian/sentinel/evaluator and daemon/runner identities, exact native model digest and effective context, placement/domain and fresh available bytes. None availability with a known envelope is `telemetry_unavailable`; invalid/failed telemetry is ERROR. Inventory/free host RAM cannot substitute.
- Envelope and runtime identity mismatches refuse separately from policy identity mismatch. Every non-admitted local status refuses before credentials/client construction. Synthetic positive tests certify machine wiring only; `hardware_safe` stays unknown.
- **Memory-checked Test (no policy configured).** Only the Settings Test (`connection_test.test_connection`/`test_tasks`, serialized by `SetupAttempt.run`; `probe_provider(..., memory_probe=True)`) skips `effective_envelope_unknown`: `local_memory_admission.py` takes Ollama's own size for the model (`/api/tags`; unknown or non-Ollama → `model_size_unknown`), a fresh `psutil` reading (failure → `memory_telemetry_unavailable`) and admits only if available RAM ≥ 1.25 × size + a KV allowance (`num_ctx` × 256 KiB, recorded in `thresholds.kv_allowance`) + 4 GiB (a model listed by `/api/ps` needs 0 for the size term, floors still apply) and free swap ≥ max(2 GiB, 15% of total) (`memory_insufficient`, `swap_nearly_full`; no swap → floor n/a). Refusals precede any client/credential and carry the real numbers in `detail`; `admission.evidence` (RAM, swap, footprint, thresholds, `taken_at`) is kept in the receipt, `hardware_safe` stays `unknown`. The request is re-read before sending, capped at 16 reply tokens, and the model is unloaded afterwards (`keep_alive: 0`, only if absent from `/api/ps` at admission and listed there now) so an unloaded refusal never triggers a load. A configured policy file keeps the path above unchanged; Doctor, the admin `llm-check` route, the wizard, the CLI, dry-run and calibration keep refusing UNKNOWN (`memory_probe` defaults to False).
- Doctor's existing configuration signature includes policy bytes, configured path/hash and admission revision, never volatile memory samples. Changing approved bytes (even same-length), approval or revision invalidates old Ready; status reads do not approve or rewrite files.

Explicit preparation reuses installed weights through hard links or cross-volume
symlinks in selected `Settings.data_dir`, with private manifests/parameters and a
separate loopback daemon. Finite guard and runtime both default to4GiB combined RSS;
an explicit operator-selected `rss_limit_mib` permits a matching larger guard cap
for that scope only. The20% free-memory/2GiB swap-growth caps and pipe-bound sentinel
remain unchanged; no ordinary app setting, automatic escalation or fallback is added. The user's daemon/store and
saved routes remain untouched. Load/unload have distinct owned native terminals;
load-only completion cannot mark inference operational. Policy bytes/approval are
still independently required before inference and are not created by the scope.
Every owned inference send rechecks admission, not just construction. Native unload
requires empty residency; owned process cleanup does not fabricate terminal evidence
or clear HOLD. This is an operator-only scope, not warming on navigation/save or a
new automatic calibration/connection endpoint. The current isolated serving
candidate defaults to batch128 through a private native parameter manifest; the
explicit owned scope can select a smaller batch for a bound measurement and verifies
the actual model-process arguments. Allocated K/V cache types must also agree with
the chosen f16/q8/q4 precision; quantized modes require Flash Attention. q4 is a
candidate with possible quality loss, not an automatic fallback. It changes
configuration, not model weights;
its memory/latency benefit still requires a legitimately admitted live measurement.
The scoped native host prompt-state cache is disabled and observed as disabled,
rather than retaining the backend's8GiB cache default inside a4GiB finite envelope.
K/V context and source/verifier gates remain unchanged; the original daemon is untouched.
The explicit scope can forward the existing provider thinking-effort setting before
runtime identity and policy binding; defaultoff is unchanged. No inference on save,
new UI setting, in-place identity mutation or native-reasoning-depth certificate.
The declared output budget is likewise forwarded before binding; the known native
backend uses its supported `max_tokens` compatibility field, not an unenforced SDK
alias. This does not alter other provider dialects or saved configuration.
Recovery from a stuck local request: delete `data/setup_doctor.json`.

The shared provider predicate includes all literal 127/8 and IPv4-mapped IPv6
loopbacks, numeric IPv4 shorthand and the reserved `.localhost` namespace,
with no DNS lookup. Both probe and explicit verification factory paths
refuse before credentials/adapters. Verification revision v4 invalidates pre-policy
Ready. Inventory recovery uses the failing provider/model/endpoint, not any sibling:
Ollama suggestions apply to that local OpenAI-compatible endpoint on port 11434;
other runtimes open AI Settings and choose a served model. Model-error prose cannot
select a runtime or produce a pull command; actual inventory recovery is retained.


The web wizard chooses Full local, Hosted or No LLM **before** Zotero paths;
ML-only skips the model step. Full-local mode shows compatible advisory profiles with
no mandatory API-key field; preparing one still requires Connect/impact/Use. Doctor's fresh-process asset probe not only enables HF
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
RSS check supplies typed `open_sources_settings` recovery with a task-neutral
“Add an RSS source” label; web navigation opens Today’s canonical RSS editor without
running verification or refreshing sources. The recovery enum and read-only check
remain unchanged.

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
It reuses `qa_body_text`: PDF-tier `full_text` is admitted, a dedicated `qa_text`
takes precedence, and explicit empty QA text cannot fall back to presentation.
Operational response templates are refused before model work. Tier2 calls the
production digest strategy with its separate light verifier, original-source audit,
formatting and map-stage routing; all constructed clients are closed, including
hosted clients and partial construction failures. This is still a text-budget
sweep, not output-token/effort tuning, backend-availability certification, or a
population-level reliability/quality guarantee. A failed sweep is not calibrated.
ML-only mode marks model/inference/dry-run checks as intentionally skipped and
keeps classifier/search triage available; it cannot generate the full-text
review required before an app-controlled feed Add. Manual Zotero imports remain
possible, and AI can be enabled later in Settings.

Advisory Zotero readiness reads run off the API event loop, so a busy native DB
cannot consume an unrelated model-reachability deadline. Failed library statistics
skip the redundant feed-count read; typed feed-read failures remain an explicit
Zotero status error rather than failing the whole readiness response. No native
writes, timeout widening or database retry changes are involved.

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
