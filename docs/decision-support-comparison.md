# Opt-in offline criterion–original-evidence comparison (#37)

## Invocation and state

Run from the source checkout with the existing Python dependencies installed:

- `python -m tools.method_validation support --config /absolute/config.json`:
  read-only admission, zero HTTP/model calls and zero artifact writes.
- Add `--run` for explicit private-write and configured-native-HTTP consent.
- `python -m tools.method_validation support --inspect /absolute/attempt-directory`:
  validated replay, no network/model work or writes. `--inspect --run` is invalid.

All artifacts are private under the explicitly selected
`Settings.data_dir/support_comparison/<attempt>`. Settings loading disables `.env`;
no app bootstrap, keyring, account discovery or default backend is used. The parent
owns actual live-run resource admission and endpoint qualification. A config string
asserting an external guard does not install or prove that guard. There is no server
launch, model download, retry, fallback, repair, dataset acquisition or label mutation.

## Frozen configuration and public inputs

The complete strict schema is `tools/method_validation/support.py`; the public seam
is [INTERFACES.md](../tools/method_validation/INTERFACES.md). There is no hidden second
configuration or executable prompt asset. Required configuration contains:

- Absolute `project_root`; unique safe `attempt`; hash-bound absolute `inputs` file.
- Explicit `arms`, `baselines`, baseline `reference`, separate `generator_handle` and
  `grader_handle`. Each arm has its own model/display name and native/recorded/blocked
  kind. No model or deployment is chosen for the caller.
- An input JSON containing original sources (paper key, canonical family, development
  or holdout split, literal original text), strict cases, separate grader-only gold,
  optional recorded terminal file references, split/holdout timestamps and a synthetic
  flag. File references require absolute paths and exact raw-byte SHA-256.
- Baseline artifacts with explicit source/model/input binding. Only criterion/excerpt
  matches are admitted. Historical MiniCheck headline/retrieved-chunk outputs are not
  silently relabelled as the same criterion/original-span task. Missing matched
  artifacts remain unavailable, not evidence against the baseline.

Use the public synthetic fixture authoring example in `tests/test_support_fixtures.py`
for the actual schema and hash construction; it is test-only wiring data, never a
production dataset or human study. Cases use `sha(text.encode('utf-8'))` for literal
hashes and explicit Unicode-character offsets. Gold uses `gold_identity`, not pair
indices; baseline rows use `digest(case.model_dump())`. Artifact owner hashes pin
`quality_eval.py` and `claim_checker.py` on the intentional main base. Source pinning
and an artifact's source-reference declaration do not authenticate its acquisition.

Native and MiniCheck thresholds require calibration provenance: the exact ordered
paper/family/split digest, development-only families and method, frozen after the split
and before holdout opening. Whole-paper-family/canonical-source overlap is rejected.
No examples or gold fields are accepted in generator cases, and no gold-derived
threshold fitting occurs. Gold and all provenance stay outside captured native requests.

`ZS_SUPPORT_OVERRIDES` is a JSON object overlay on the single config; replacing an
arm list replaces the whole list. The effective validated config and exact original
config bytes are both retained. Unknown fields, duplicate JSON members, nonfinite
numbers, wrong input hashes and oversize admission fail before model calls/writes.

Recorded replay binds the per-row copied terminal to the exact admitted
`(case_sha256, arm_id)` recording and derives its decision from those bytes. The
recorded received state cannot be relabelled as a transport failure. Recomputing
copied-terminal/result/report hashes and ACK alone cannot replace an unchanged
frozen recording; this input-to-output check is separate from signed authenticity.
Omitting a terminal reference cannot orphan acknowledged terminal bytes. A row
without a terminal must exactly reconstruct an admission-permitted blockage,
missing recording or wall exhaustion and its request hash, empty result and
finite `telemetry.remaining_seconds` observation. Wall exhaustion remains in the
evaluated denominator. Transport statuses are restricted to the adapter's emitted
domain; a transport terminal cannot claim predispatch blockage. Remaining time is
an unsigned observation, not authenticated execution or external resource proof.

## Bounds and resource accounting

Defaults: at most 120 pairs (maximum 10,000), 32 total arms, 12,000 full-prompt
characters (maximum 131,072), 65,536 request/response bytes (maximum 1 MiB each),
8 MiB total frozen-input bytes (maximum 64 MiB, shared by inputs, baselines and
recordings), 200 logical HTTP calls (maximum 10,000), and
400 reserved forward-pass work units. Native qualified arms declare a per-request
forward-pass upper bound; the entire call/work reservation must fit before execution.
The bound is caller-declared, not an observed physical-pass count. Public native
entrypoints also require consent/guard and reserve at least their next call; one adapter
instance enforces its cumulative call/work limit. The runner owns whole-workload
reservation; separate direct invocations do not share process-global counters.
Recorded evaluation accepts only an unblocked recorded arm. Off-host endpoints require
HTTPS; explicit loopback HTTP remains available for local llama.cpp. Oversize pairs,
source drift and total reservation failures refuse the whole workload, without clipping
or partial model execution. Config files are bounded at 1 MiB in the CLI.

HTTPX operation/idle timeout defaults to 120 seconds, maximum 600. It is not an
aggregate deadline: active response traffic can exceed it. The between-call wall
safety defaults to 3,600 seconds, maximum 7,200; `ZS_SOURCE_WORK_SAFETY_MS` may lower
it (default 3,600,000, maximum 7,200,000). It is a safety net, not the workbudget or
an installed watchdog. The external parent-owned hard wall/memory guard remains
necessary for live HTTP. `public_input_consent=true` is additionally required for
qualified live arms; do not transmit private inputs under public-input consent.

Each known logical attempt records reservation separately from attempted HTTPX
dispatch, elapsed time, HTTP status, client retries (zero), raw completeness and retained
bytes. Dispatch is an HTTPX dispatch attempt, not proof all bytes reached a server.
Every known terminal is captured before parsing; oversize/partial terminals retain only
the bounded observed prefix with an explicit incomplete status. Encoded responses are
kept raw and unscored, not transparently decompressed. Known transport failures remain
unscored rows; programmer or persistence errors propagate and mark the attempt failed.
No silent re-use follows a writer exception. There is no resume of partial/pending runs.

Observed native usage remains in the original readout. Known input-token statistics
include received valid readouts, including unscored model-mismatch/threshold outcomes;
missing usage is not zero. Physical retries/forward passes, cold/startup latency,
server peak memory and cost are null. A single HTTP request may perform multiple
model-specific passes. `output_tokens=0` is not zero compute, memory or cost.

## Source-backed native boundary

Only the pinned llama.cpp **b11430**, commit
`8345f333951c661d166b00e6f9362e553768f292`, MIT text-choice subset is implemented.
The actual primary source packet verifies `POST /v1/systemone`, string state,
`questions.decision` with type/instructions/null-keyed criteria, and root model,
answers and usage. Response `decision` requires type/choice/probabilities/confidence;
usage requires integer input tokens and output tokens exactly zero.

The full bounded raw terminal precedes duplicate-member/strict-schema/finite-vector
validation. Missing/foreign options, invalid ranges or sums, malformed data, wrong
model alias, timeout and unavailable endpoints never become semantic insufficiency.
The native choice is preserved even when different from an independently computed
argmax. Abstention uses that choice's probability, not the provider confidence.
Model aliases do not attest checkpoint/version/GGUF/quant/runtime identity.

All arms use lexical criterion-key order because CLEF sorts keys, while the report
vector domain remains supports/contradicts/insufficient_evidence. Actual model ordering,
context/micro-batch fit, checkpoint hash, quant, licence eligibility, runtime source and
access must be separately qualified. Unknown native arms are visibly blocked and
unevaluated, not negative semantic examples or manufactured operational evaluations.
TypeSafe hosted/auth variants and OpenJev author-server variants are not implemented;
record them blocked unless independently supported by this exact native protocol.
No installed endpoint, real smoke test, model access or deployment follows from docs.

Design transfer: ARE judgment at `a3bc20c4580c6fe5d7528fd9b2a8da778f5ea395`
separates unknown from false/typed failure; this tool closes that boundary before
quality metrics but does not adopt ARE's event DAG/judge/runner. Codex
`67a709...` typed event projection versus raw data and owning publication ACK inform
raw preservation/exclusive publication only, not scientific/Codex verifier parity.
Reference inspection is source evidence, not scientific validation.

## Report interpretation and open study

Per-arm holdout reports give actual statuses/coverage/denominators, false confirmations
among nonsupport gold, false rejections among support gold, binary loss mean and median,
per-binary-class counts and qualified native three-way per-class/Brier/calibration bins.
MiniCheck scalar binary Brier is separate; a verifier rejection is not contradiction.
Native confidence bins are descriptive, never cross-model calibrated probabilities.
Empty metrics are null. Blocked arms are unevaluated with null coverage/error metrics.
Latency summaries include every dispatched success/failure, not success-only samples;
recorded or imported arms have no invented runtime latency. Warm/cold qualification
is unknown; the separate warm-latency/resource gates stay null.

Paired comparison uses only jointly decided holdout identities whose frozen gold has
non-null human provenance. Unprovenanced labels remain in descriptive per-arm metrics,
not paired estimates/noninferiority; matched coverage still uses all candidate holdout
pairs as its denominator. Provenance is a declaration, not authenticated human review. It
reports matched coverage and disagreements, the equal-paper-family mean binary-support
loss difference, and a seeded percentile family-bootstrap 95% CI only above both
predeclared floors (at least 80 pairs and 20 independent families). This is not a BCa
F1/ratio CI, a three-class baseline comparison, or an unpaired paper-level precision
claim. Coverage and quality denominators are separate; missing labels never become
correct decisions. Configured error, support-coverage, operational, noninferiority and
resource gates produce an inspectable no-go/inconclusive result. Because this minimum
comparator has no independently measured cold/memory/cost campaign, it always retains
the baseline and cannot claim a task winner or automatic promotion.

The 80–120 actual human-labelled pairs, untouched whole-paper holdout, development
calibration, live baseline runs, real endpoint/version/licence qualification, reviewed
disagreements and complete matched resource campaign remain open/unperformed. Public
synthetic MockTransport tests establish wiring only. #38/#42 and live product verifier,
quality grade, labels, ranking, MiniCheck models/caches and golden CSV remain unchanged.
