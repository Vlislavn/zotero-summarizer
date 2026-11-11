"""Attempt-local metadata; sensitive decoded model values require CLI consent."""
from __future__ import annotations

from copy import deepcopy
from contextvars import ContextVar
from functools import lru_cache, wraps
from importlib import import_module
from importlib.util import find_spec
import hashlib
import json
from pathlib import Path
import threading
from time import perf_counter
from typing import Any
from uuid import uuid4

from zotero_summarizer.integrations.llm import LOGGER, InstrumentedLLMClient
from zotero_summarizer.integrations.llm_callbacks import LLMOutputTruncated, observe_completions
from zotero_summarizer.services.library import _call_guard


_CURRENT: ContextVar[Attempt | None] = ContextVar('review_attempt', default=None)


def identity(value: str) -> dict[str, Any]:
    return {'sha256': hashlib.sha256(value.encode()).hexdigest(), 'characters': len(value)}


def file_identity(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
            size += len(chunk)
    return {'sha256': digest.hexdigest(), 'bytes': size}


def _decoded(value: Any) -> Any:
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, (list, dict)):
        return deepcopy(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {'type': type(value).__name__}


def _token_counts(usage: Any) -> dict[str, int]:
    """Prompt/completion/cached/reasoning tokens from a provider usage blob.

    Cached and reasoning tokens are the two numbers that decide whether the pipeline
    is fast and whether thinking is really off (#132/#138/#141)."""
    if not isinstance(usage, dict):
        return {}

    def _nested(name: str, key: str) -> Any:
        value = usage.get(name)
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    counts = {
        'prompt_tokens': usage.get('prompt_tokens'),
        'completion_tokens': usage.get('completion_tokens'),
        'cached_tokens': usage.get('cache_read_input_tokens') or _nested('prompt_tokens_details', 'cached_tokens'),
        'reasoning_tokens': _nested('completion_tokens_details', 'reasoning_tokens'),
    }
    return {key: int(value) for key, value in counts.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)}


class Attempt:
    def __init__(self, *, capture: bool = False, directory: Path | None = None, max_in_flight: int = 0):
        self.directory = directory
        # One review's provider budget: parallel branches and their inner pools all queue here.
        self.slots = threading.BoundedSemaphore(max_in_flight) if max_in_flight > 1 else None
        self.capture = capture
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.token = None
        self.started: float | None = None

    def __enter__(self):
        self.started = perf_counter()
        self.token = _CURRENT.set(self)
        return self

    def __exit__(self, _exc_type, exc, _traceback):
        if exc is not None:
            from zotero_summarizer.services.library._deep_review_errors import diagnostic_for_exception

            self.record('terminal', diagnostic_for_exception(exc), kind='failure_identity')
        _CURRENT.reset(self.token)
        if self.directory is not None:
            self.save(self.directory)

    def record(self, stage: str, value: Any, *, kind: str):
        decoded = _decoded(value)
        text = decoded if isinstance(decoded, str) else json.dumps(decoded, sort_keys=True, ensure_ascii=False)
        event = {'stage': stage, 'kind': kind, **identity(text)}
        if kind == 'parsed_response':
            from zotero_summarizer.models import PaperDigest

            if isinstance(value, PaperDigest):
                event['supplied_digest_fields'] = sorted(value.model_fields_set & PaperDigest.model_fields.keys())
        if kind in {'identity', 'failure_identity', 'output_budget_retry'} and isinstance(decoded, dict):
            allowed = {'from_max_tokens', 'to_max_tokens', 'sha256', 'characters', 'bytes', 'provider', 'model',
                       'prompt_schema_version', 'strategy', 'max_chars', 'code', 'stage', 'recovery',
                       'scope', 'artifact', 'availability', 'outcome', 'counts_as_verified_review', 'http_status', 'reason_code',
                       'expected_checks', 'returned_checks', 'verifier_attempt'}
            event['identity'] = {key: value for key, value in decoded.items() if key in allowed}
        if self.capture:
            event['decoded_value'] = decoded
        with self.lock:
            self.events.append(event)

    def record_call(self, stage: str, seconds: float, usage: dict[str, int]) -> None:
        """One LLM call: stage, wall seconds and token counts (never prompt text)."""
        event = {'stage': stage, 'kind': 'call', 'seconds': round(seconds, 3), **usage}
        with self.lock:
            self.events.append(event)

    def timings(self) -> dict[str, Any]:
        """Per-stage calls/seconds/tokens plus the review wall time and route."""
        with self.lock:
            events = list(self.events)
        stages: dict[str, dict[str, Any]] = {}
        routing: dict[str, Any] = {}
        for event in events:
            if event['kind'] == 'call':
                row = stages.setdefault(event['stage'], {
                    'calls': 0, 'seconds': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0,
                    'cached_tokens': 0, 'reasoning_tokens': 0})
                row['calls'] += 1
                row['seconds'] = round(row['seconds'] + float(event.get('seconds') or 0.0), 3)
                for key in ('prompt_tokens', 'completion_tokens', 'cached_tokens', 'reasoning_tokens'):
                    row[key] += int(event.get(key) or 0)
            elif event['stage'] == 'routing' and event['kind'] == 'identity':
                routing = dict(event.get('identity') or {})
        for row in stages.values():  # completion tokens (incl. reasoning) per call second, prefill included
            row['tok_per_s'] = round(row['completion_tokens'] / row['seconds'], 1) if row['seconds'] else None
        return {
            'total_seconds': round(perf_counter() - self.started, 1) if self.started else None,
            'calls': sum(row['calls'] for row in stages.values()),
            'provider': routing.get('provider'), 'model': routing.get('model'),
            'stages': stages,
        }

    def metadata(self) -> dict[str, Any]:
        with self.lock:
            events = [{k: v for k, v in event.items() if k != 'decoded_value'} for event in self.events]
        counts: dict[str, int] = {}
        for event in events:
            if event['kind'] == 'call':
                continue
            key = event['stage'] + ':' + event['kind']
            counts[key] = counts.get(key, 0) + 1
        return {'events': events, 'stage_counts': counts}

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        payload = {'contract': 'decoded-provider-values-not-raw-transport',
                   'metadata': self.metadata(), 'events': self.events if self.capture else []}
        path = directory / 'attempt.json'
        with path.open('x', encoding='utf-8') as handle:
            path.chmod(0o600)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return path


def output_retry_stages() -> list[str]:
    """Stages of the current attempt that were re-run once with a larger output budget."""
    attempt = _CURRENT.get()
    if attempt is None:
        return []
    with attempt.lock:
        return [event['stage'] for event in attempt.events if event['kind'] == 'output_budget_retry']


def record(stage: str, value: Any, *, kind: str = 'source') -> None:
    attempt = _CURRENT.get()
    if attempt is not None:
        attempt.record(stage, value, kind=kind)


@lru_cache(maxsize=1)
def _transport_failures():
    import httpx
    from zotero_summarizer.api.errors import LLMTimeoutError

    types = [httpx.HTTPError, TimeoutError, ConnectionError, LLMTimeoutError]
    for name in ('openai', 'anthropic'):
        if find_spec(name) is not None:
            sdk = import_module(name)
            types.extend((sdk.APIConnectionError, sdk.APIStatusError))
    return tuple(types)


def observed_client(client: Any, stage: str | None = None) -> Any:
    """Stage-labelled view of ``client`` so every call is timed and token-counted.

    ``stage=None`` keeps whatever label the wrapper already carries (nesting is
    unwrapped, never double-recorded), so a layer can label a whole block of calls
    while a specific call site keeps its own name."""
    if isinstance(client, _ObservedClient):
        return _ObservedClient(client.client, stage or client.stage, client.attempt)
    attempt = _CURRENT.get()
    return _ObservedClient(client, stage or 'llm', attempt)


class _ObservedClient:
    def __init__(self, client: Any, stage: str, attempt: Attempt | None):
        self.client, self.stage, self.attempt = client, stage, attempt

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _run(self, call, client):
        """One guarded provider call on ``client``; provider failures become typed review errors."""
        from zotero_summarizer.api.errors import APIError

        def once():
            usage: dict[str, int] = {}

            def observe(response: Any) -> None:
                usage.update(_token_counts((response.llm_output or {}).get('token_usage')))

            with observe_completions(observe):
                return call(client), usage

        try:
            return _call_guard.run(once, self.stage, self.attempt.slots if self.attempt is not None else None)
        except ValueError as exc:
            if self.stage.startswith('verifier'):
                raise
            from zotero_summarizer.services.library.quality_review import DigestGenerationFailed

            raise DigestGenerationFailed(str(exc)) from exc
        except _transport_failures() as exc:
            from zotero_summarizer.services.llm.probe_failure import failure_observation

            observation = failure_observation(exc, {'invalid_events': False, 'coherent': False})
            details = {'stage': 'verification' if self.stage.startswith('verifier') else 'generation',
                       'recovery': observation['detail'], 'reason_code': observation['reason_code']}
            if 'http_status' in observation:
                details['http_status'] = observation['http_status']
            raise APIError('review_provider_unavailable', observation['detail'], 503, details) from exc
        except Exception as exc:  # Provider boundary: retain the original type and actual call origin.
            exc.review_stage = ('correction' if self.stage == 'correction'
                                else 'verification' if self.stage.startswith('verifier') else 'generation')
            raise

    def _run_larger(self, call):
        """After finish_reason=length: ONE re-run of the identical call with a bounded larger output
        budget. ``None`` when none is possible (unknown/ceiling budget, other client types, map chunks
        — those subdivide instead)."""
        if self.stage == 'map' or not isinstance(self.client, InstrumentedLLMClient):
            return None
        larger = self.client.with_larger_output()
        if larger is None:
            return None
        LOGGER.warning("LLM output hit the limit stage=%s max_tokens=%s; retrying once with %s",
                       self.stage, self.client.max_tokens, larger.max_tokens)
        if self.attempt is not None:
            self.attempt.record(self.stage, {'from_max_tokens': self.client.max_tokens,
                                             'to_max_tokens': larger.max_tokens}, kind='output_budget_retry')
        try:
            return self._run(call, larger)
        except LLMOutputTruncated as exc:
            exc.output_budget_retried = True
            raise
        finally:
            larger.close()

    def _invoke(self, call, prompt, kind):
        if self.attempt is not None:
            self.attempt.record(self.stage, prompt, kind='prompt')
        try:
            (value, usage), seconds = self._run(call, self.client)
        except LLMOutputTruncated:
            retried = self._run_larger(call)
            if retried is None:
                raise
            (value, usage), seconds = retried
        tok_per_s = round(usage['completion_tokens'] / seconds, 1) if usage.get('completion_tokens') and seconds > 0 else None
        if self.attempt is not None:
            self.attempt.record_call(self.stage, seconds, usage)
            self.attempt.record(self.stage, value, kind=kind)
        LOGGER.info(
            "LLM call stage=%s seconds=%.1f prompt_tokens=%s completion_tokens=%s "
            "cached_tokens=%s reasoning_tokens=%s tok_per_s=%s",
            self.stage, seconds, usage.get('prompt_tokens'), usage.get('completion_tokens'),
            usage.get('cached_tokens'), usage.get('reasoning_tokens'), tok_per_s,
        )
        return value

    def pydantic_prompt(self, *, prompt, **kwargs):
        return self._invoke(lambda client: client.pydantic_prompt(prompt=prompt, **kwargs), prompt, 'parsed_response')

    def prompt(self, prompt):
        return self._invoke(lambda client: client.prompt(prompt), prompt, 'returned_value')


def current_metadata() -> dict[str, Any] | None:
    attempt = _CURRENT.get()
    return attempt.metadata() if attempt is not None else None


def current_timings() -> dict[str, Any] | None:
    attempt = _CURRENT.get()
    return attempt.timings() if attempt is not None else None


def tracked_worker(worker):
    @wraps(worker)
    def run(item, *args, **kwargs):
        ctx = args[0] if args else kwargs.get('ctx', {})
        with Attempt(max_in_flight=int(ctx.get('sub_concurrency') or 0)):
            config = ctx.get('config')
            if config is not None:
                record('config', identity(config.model_dump_json()), kind='identity')
            provenance = ctx.get('provenance') or {}
            record('routing', {key: provenance[key] for key in ('provider', 'model', 'prompt_schema_version')
                               if key in provenance}, kind='identity')
            return worker(item, *args, **kwargs)
    return run


def attempt_directory(data_dir: Path) -> Path:
    return data_dir / 'deep_review_attempts' / uuid4().hex


def publishable_entry(entry: dict, prior: dict | None, acquired: Any) -> bool:
    from zotero_summarizer.services.library.review_eligibility import usable_review

    unavailable = bool(entry.get('needs_pdf'))
    record('terminal', {
        'outcome': 'source_unavailable' if unavailable else 'published_digest',
        'counts_as_verified_review': not unavailable and usable_review(entry),
        'availability': acquired.outcome if acquired is not None else 'not_observed',
    }, kind='identity')
    return not unavailable or not usable_review(prior)
