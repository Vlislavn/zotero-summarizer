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
#38/#42 need their own task projections; #49 needs scientific-warrant evaluation,
not this contract's three-way structural checks.
