# method_validation — experimental developer client (not shipped)

```
parent's public run -> prompt / pydantic_prompt -> neutral ChatOpenAI
                                             -> hash-bound Pi auth + payload helpers
                                             -> fixed Codex Responses HTTPS endpoint
                   <- complete text / parsed object + safe call receipts
```

The client adds no product provider registration, agent loop, dataset reader,
grader, persistence, async API, model fallback or automatic repair. Parent owns run admission, public
input consent, generator/judge separation, app prompts and app repair budgets.
A marker probe is transport evidence, not paper/schema readiness.

## Contract

Construct `ClientOptions` with caller-owned `model`, `reasoning_effort`, stable
`session_id`, explicit `max_logical_calls`, absolute `source_path` and **full**
expected `source_sha256` for the existing `pi_codex_transport.py`. No home path is
hardcoded. Constructor verifies and executes those exact dependency bytes; import
and construction resolve no bearer, make no requests and load no model weights.
The dependency is rechecked before each logical call. `gpt-6.1-sol` requires `xhigh`.

`MethodValidationClient(options, observer=callback)` implements the synchronous
app `LLMClient` protocol; use a context manager or `close()`. Calls are serial,
non-reentrant and bounded by `max_logical_calls` (maximum 10,000). There is **one
attempt**, SDK retries **0**, stream retries **0**. No repeated-compute/idempotency
claim. Construction lazily creates only an owned synchronous HTTP client;
LangChain's otherwise-unused async client is explicitly disabled. Global model
caching is disabled, so a cached response cannot masquerade as an inference.
At both public call boundaries, `tracing_context(enabled=False, parent=False)`
plus an owned local LangChain tracer ContextVar token suppress automatic remote
tracing (including OnPrem's PromptTemplate invocation) and restore outer contexts
on success/failure. Callbacks are empty. This is not suppression of arbitrary installed callback-hook code.

The named HTTPX **operation/idle timeout**, `operation_timeout_seconds`, defaults
to 120, maximum 600; `ZS_METHOD_OPERATION_TIMEOUT_SECONDS` overrides the default.
It is **not** an aggregate/hard deadline: active streaming can exceed it. Every
live run requires the parent's external process-wide wall/RSS/free-RAM/swap-growth
guard. This module creates no watchdog or runner.

Only the exact `https://chatgpt.com/backend-api/codex/responses` destination is
allowed before token resolution. Existing Pi token/auth helpers capture credentials
in memory; this client has no API-key/alternate URL configuration. Redirects and
HTTP proxy environment are disabled. Explicit output limits, `response_format`,
nonempty `stop`, `temperature` and all other unsupported kwargs fail before
construction/network; only empty `stop` (`None`/`[]`) is harmless. Do not wrap this
client with `InstrumentedLLMClient(structured_output=True)`.

Plain prompt bytes are untouched. Pydantic calls reuse the installed OnPrem
`LLM._format_pydantic_prompt` **unbound**, its parser and `helpers.extract_json`,
never its constructor/`OutputFixingParser`. This preserves OnPrem's prompt-schema
route and parser behavior (including its existing lenient JSON parsing).
Observed `finish_reason=length` retains the existing typed `LLMOutputTruncated`
boundary after emitting the safe receipt. Unparseable output returns the original
raw text with `status=parse_failure`;
it is not a parsed success or fabricated null/model. App-owned repair remains
unchanged. Biomni XML truncation is **never** called; literal `</execute>` and
`</solution>` in paper JSON survive.

## Receipts and safety

The observer receives `call_started` and `call_terminal` dictionaries containing
original/request prompt separately, complete returned text, source binding,
logical-call number, configured/observed model, session, declared bounds and safe
attempt metadata. `generated_request.scope=generated_payload` is not wire proof;
`requests[].scope=httpx_request_hook_pre_dispatch` records the serialized HTTPX
request's model/effort/session/store/stream just before dispatch, **not** proof of
successful network delivery. Successful model identity must match configuration;
a safe terminal receipt is emitted **before** rejection of mismatch or failure.

Usage contains provider-derived input/output/total/cache-read/reasoning values;
missing fields (especially cache-creation) are `null`, never estimated or invented
zero. Incomplete transport attempts have unavailable usage, not zero consumption.
No credentials, headers, private auth/account claims, serialized clients, opaque
provider metadata or raw exception strings are sent to the observer. Callback
failures are fatal and never retried; persistence/admission/secret scans are the
parent's responsibility. Prompts and output are deliberate capture fields: admit
only consented public inputs; this is not a privacy sandbox.

## Source decisions and checks

Close existing auth, native model/effort/session and request normalization; diverge
Biomni truncation, schema/output-limit dropping and default retries; defer native
output schemas, routing, async and execution frameworks. References: Codex
`core/src/client.rs:908-1013`, `protocol/src/error.rs:376-429`, ARE
`are/simulation/replay.py:18-79`; the parent retains revision/license/source receipts.

Focused synthetic tests: `tests/test_method_validation_client.py`,
`tests/test_summarization_repair.py`, `tests/test_refine_prompt_source_status.py`.
The test-only AST formatting fixture executes exact SHA-bound pure OnPrem bodies;
it is **not** real-import or live proof. Primary local evidence also records an
isolated real import/constructor/formatter denial proof and installed source
SHA/package versions. No live requests or actual paper outputs were validated.
See [CHANGELOG](../../CHANGELOG.md).

`practitioner.py` exposes one RSS arm: strict generator fields and
`ZS_PRACTITIONER_CONTRACT`, Sol/xhigh, ≤3 calls, ≤600s outer guard.
Real routing/prompts/scoring; started calls outrank generated tags.
Zero-call gate/missing-input markers remain. No live/semantic proof.

Diagnostic reopen verification compares the complete validated public JSON
projection, not Pydantic model equality: private gate provenance intentionally
cannot survive public JSON. Public-field mismatches still fail; verification
neither restores private authority nor overrides model equality. Existing
zero-call outcomes and started-call precedence are unchanged. The original gate
regression and valid public-field tamper cases live in
`tests/test_practitioner_runtime16.py`; client contracts remain in
`tests/test_method_validation_client.py`.

## Criterion/evidence decision contract (#37)

```
caller-frozen original extraction + generator-only pair
    -> exact source/span admission -> escaped criterion/excerpt prompt
    -> ONE injected native adapter -> structural result (not a judgment)
```

`support.py` adds `admit_support_case` and `evaluate_support`; it is not a CLI,
endpoint adapter, evaluator/grader framework or production verifier. Reuse
faithbench's `sha256_text` and the shared escaped untrusted-input boundary. Source
and excerpt hashes bind UTF-8 text; offsets index Unicode characters in the exact
frozen extraction. Repeated excerpts use their explicit occurrence. No trimming,
fuzzy matching, regenerated summaries, gold fields or source substitution. Literal
binding does not certify source acquisition, originalness or scientific support.

`SupportArm` requires explicit model/runtime identity, full-prompt character cap,
probability-sum numerical tolerance and nullable abstention threshold. These are
caller-frozen inputs, not recommended thresholds or runtime permission. Oversized
prompts are unattempted failures, never clipped. Options are always `supports`,
`contradicts`, `insufficient_evidence` in that order. Only criterion/excerpt enter
the prompt; labels and private/gold fields have no place in the strict projection.

An injected adapter returns `ProviderReply` with complete raw response text and a
native readout. All three finite [0,1] probabilities and the native selected label
are required; preserve values without normalization, argmax replacement or invented
rationales. A single-confidence/binary baseline cannot fabricate this vector.
The result retains input, arm, prompt/readout-schema hashes, raw response and model
identity. Semantic insufficiency is a decision, not threshold abstention. Malformed
vectors, model mismatch, timeout, unavailable endpoint and oversize are unscored
outcomes. Known transport errors get one failure row without exception text;
programmer/adapter faults propagate. No retry, file/store/write, model load, network
client, metric or default-product change is supplied. Persist/reopen via existing
caller-owned manifest/journal boundaries; result validation preserves status and
call-count invariants. Runtime identity is caller-declared, not measured here.

Source references actually inspected: ARE `simulation/validation/tool_judge.py`
542-631 (hard-before-soft) and `judgment.py` 13-104 (typed failure distinctions).
Use these as separation patterns, not a transfer of semantic-equality shortcuts.
`tests/test_support_decision_contract.py` uses synthetic scripted adapters only.
Real adapters require actual endpoint/schema/version/licence proof; the 80-120
human-labelled pairs/20-paper split, development-only calibration, baseline,
matched-coverage metrics/resources, separate judge and task selection remain open.
#38 still needs its own task projection; #49 needs scientific-warrant evaluation,
not this contract's three-way structural checks.

## Priority (#38)

`priority.py` freezes gold-free evidence, four native probabilities and ordinal
ranks; one injected call, explicit unscored failures. No adapter or study proof.
## One source-proven native text-choice adapter (#37/#38/#42)

```
caller-frozen SupportArm + explicit HTTP bounds + exact endpoint
    -> NativeDecisionAdapter(prompt, options) -> ONE /v1/systemone POST
    <- original JSON text + exact native choice/keyed vector/returned model
```

`decision_adapter.py` implements only llama.cpp **b11430**, commit
`8345f333951c661d166b00e6f9362e553768f292`, text `choice` schema. See
[primary source/license/SHA proof](../../reference/README.md). Source discovery is
not a live endpoint smoke or native checkpoint qualification. TypeSafe hosted,
OpenJev author-server variants, score/noul, images and model-router selection are
unsupported; no chat-as-native fallback, registration, scorer or second framework.

Construct `NativeDecisionAdapter(arm, DecisionAdapterOptions(...), transport=...)`.
The arm is the existing frozen `SupportArm`: model/runtime identity, prompt-character
cap, sum tolerance and optional consumer abstention policy. Options require an exact
caller-owned HTTP(S) `/v1/systemone` URL, positive `max_logical_calls`, finite positive
`timeout_seconds`, and positive `max_request_bytes`/`max_response_bytes`. No defaults,
provider host/model, credentials, environment lookup or client construction precede
an explicit call. Transport injection is trusted code, tested with actual
`httpx.MockTransport`; arbitrary injected code cannot be certified retry-free.

Each serial call owns a fresh HTTPX client (no retained cookies), default transport
retries zero, redirects/proxy environment/auth disabled. Non-200 responses are
unavailable, never followed/retried. Request and response byte caps refuse, not clip;
encoded responses are refused before decoding. The timeout is an **idle** timeout,
not a hard deadline. Caller must separately qualify rendered token/context/microbatch
fit and obtain an outer wall/memory guard before any live execution.

State preserves the whole prompt; one `decision` question uses exact option strings
as criteria keys. Responses require the pinned envelope; duplicate JSON members,
missing/extra/unknown options, wrong IDs/types, invalid probabilities, nonunit sums,
missing/mismatched model and malformed usage fail loudly with safe
`DecisionAdapterError`, not a semantic label. Timeout/transport/HTTP failure use
`TimeoutError`/`ConnectionError`, already handled by `evaluate_support`. Successful
`ProviderReply` retains original UTF-8 JSON, selected choice (never adapter argmax),
all probabilities mapped into caller-declared order without normalization, and the
actual returned model. Confidence is structurally checked, not substituted for the
vector or interpreted as calibrated scientific truth.

The source returns a model alias/name/filename, **not an attested checkpoint or
separate version**. Exact frozen model equality does not qualify weights: pin and
verify checkpoint/quant/runtime independently. Authenticated/unavailable arms remain
NOT EVALUATED. Source controls in `tests/test_decision_native_adapter.py` are synthetic
HTTP mocks, not real SDK/native proof. Human labels, baseline/equal-budget studies,
quality/calibration/coverage/resource gates and issue closure remain open.
## Collection membership input/purge/readout contract (#42)

```
caller-frozen taxonomy + canonical-family/time split + supplied derivations
    -> validated tree/eligibility + inspectable heldout-family purge
strict heldout candidate -> ONE independent question per declared key
    -> injected ProviderReply -> native 3-vector + read-only key/status projection
```

`collections.py` is one non-writing component, not a client, CLI, evaluator,
embedding baseline or pending-change integration. Construct `CollectionPlan` with
all nodes (stable key, name, explicit parent, full name path, topical/project/
workflow role and development-only description), explicit eligible keys and
ordered question keys. Duplicate display names are allowed, even identical paths;
keys alone own identity. Unknown parents/keys, cycles, inconsistent paths,
duplicate keys and workflow eligibility fail. A topical node named Inbox is not
excluded by its name. Full-tree mode questions every eligible key in order;
frozen-shortlist mode requires explicit provenance. Reuse the **same plan** across
arms; no per-arm selector, gold injection, ranking or inferred parent membership.

`purge_heldout(split_data, baseline, examples)` admits a `FrozenSplit`: every
paper key has a provided canonical-family/version identity, publication date and
split, plus a declared development cutoff and freeze provenance. Families cannot
cross splits; development dates must precede the cutoff. Older duplicate versions
of a heldout family remain heldout, not development. Duplicate representations of
the same canonical version/date are allowed. Every `DerivedRecord` declares all
source paper keys, derivation and an opaque serialized text payload. Unknown,
missing or conflicting identities fail, never fuzzy-match or silently disappear.
Any heldout source removes the whole derivation, including mixed aggregates.
The result preserves clean baseline/examples plus every removed row and its
triggering heldout identities. No corpus/library search or vector recomputation.
Completeness and truth of declared identity/lineage/freeze provenance are external
admission responsibilities, not certified by these strings or structural checks.

`evaluate_collections(data, plan, references, arm, provider,
max_logical_calls=...)` requires an exact heldout paper key in the purged split.
Its strict candidate projection contains only case/paper IDs and nullable title,
abstract/evidence; extra assignments, tags, gold or examples fail before a call.
Only candidate title/abstract/evidence and the question's eligible key/path/
description enter escaped data blocks. IDs, split metadata and **all reference
and example payloads stay outside prompts**. Descriptions/evidence must be admitted
as generator-safe by the caller; this is not a semantic leakage detector.

Reuse unchanged `SupportArm`, `ProviderReply`, probability/hash and untrusted-input
primitives. The separate membership domain always passes exact options `belongs`,
`does_not_belong`, `insufficient_evidence` in that order. Preserve the complete
native vector, native selected label, returned model and raw text; never normalize,
argmax, invent confidence or claim that a scripted adapter is native-qualified.
The caller's per-paper workbudget reserves the full question count before calls;
missing evidence/budget refusal and oversized prompts use zero calls. Other known
failures remain per-question timeout/unavailable/invalid/model-mismatch rows,
without opaque exception text; programmer faults propagate. No retries/fallback.
Callers retain aggregate budgets across papers/arms and all execution consent.

`CollectionResult` retains the plan/arm, purged-reference hash (including split and
removal audit), prompt/schema hashes and per-key native outcomes. Reopen validates
key order, call counts, native policy and summary consistency. `no_match` requires
all questions to decide `does_not_belong`; semantic insufficiency, threshold
abstention, missing input and operational failure are separate. Positive keys on
an incomplete paper are only observed positive decisions, **not actionable final
assignments**. No result enqueues/applies/creates anything or persists itself.

References inspected before implementation: public #42 authority; ARE
`are/simulation/validation/judgment.py` typed failures (close); Codex
`codex-rs/core/src/client_common.rs:23–65`, Apache-2.0, strict boundary (close).
Diverge from event/oracle/scorer machinery: this is structural projection only.
The exact Occam/Tesler vault notes motivated subtracting a second backend,
selector and hidden-gold surface. Frozen GitHub/arXiv recency148 metadata was read
as discovery, never performance evidence. Explicit-contract source loop; synthetic
machine tests are in `tests/test_collection_decision_contract.py`. Worker checks
are stdlib AST only; parent owns guarded runtime RED/GREEN and integration.
Native schemas/licences/access, shortlist recall, real embedding comparison,
~100 human-confirmed assignments, development calibration, equal-budget matched
quality/coverage/latency/memory/cost studies and END/BATCH acceptance remain open.
