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
from typing import Any
from uuid import uuid4


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


class Attempt:
    def __init__(self, *, capture: bool = False, directory: Path | None = None):
        self.directory = directory
        self.capture = capture
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.token = None

    def __enter__(self):
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
        if kind in {'identity', 'failure_identity'} and isinstance(decoded, dict):
            allowed = {'sha256', 'characters', 'bytes', 'provider', 'model',
                       'prompt_schema_version', 'strategy', 'max_chars', 'code', 'stage', 'recovery',
                       'scope', 'artifact', 'availability', 'outcome', 'counts_as_verified_review', 'http_status'}
            event['identity'] = {key: value for key, value in decoded.items() if key in allowed}
        if self.capture:
            event['decoded_value'] = decoded
        with self.lock:
            self.events.append(event)

    def metadata(self) -> dict[str, Any]:
        with self.lock:
            events = [{k: v for k, v in event.items() if k != 'decoded_value'} for event in self.events]
        counts: dict[str, int] = {}
        for event in events:
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


def observed_client(client: Any, stage: str) -> Any:
    attempt = _CURRENT.get()
    return _ObservedClient(client, stage, attempt)


class _ObservedClient:
    def __init__(self, client: Any, stage: str, attempt: Attempt | None):
        self.client, self.stage, self.attempt = client, stage, attempt

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _invoke(self, call, prompt, kind):
        from zotero_summarizer.api.errors import APIError

        if self.attempt is not None:
            self.attempt.record(self.stage, prompt, kind='prompt')
        try:
            value = call()
        except ValueError as exc:
            if self.stage.startswith('verifier'):
                raise
            from zotero_summarizer.services.library.quality_review import DigestGenerationFailed

            raise DigestGenerationFailed(str(exc)) from exc
        except _transport_failures() as exc:
            stage = 'verification' if self.stage.startswith('verifier') else 'generation'
            details = {'stage': stage, 'recovery': 'Restore the model connection and retry'}
            response = getattr(exc, 'response', None)
            if response is not None:
                details['http_status'] = response.status_code
            raise APIError('review_provider_unavailable', 'Model request failed', 503, details) from exc
        except Exception as exc:  # Provider boundary: retain the original type and actual call origin.
            exc.review_stage = 'verification' if self.stage.startswith('verifier') else 'generation'
            raise
        if self.attempt is not None:
            self.attempt.record(self.stage, value, kind=kind)
        return value

    def pydantic_prompt(self, *, prompt, **kwargs):
        return self._invoke(lambda: self.client.pydantic_prompt(prompt=prompt, **kwargs), prompt, 'parsed_response')

    def prompt(self, prompt):
        return self._invoke(lambda: self.client.prompt(prompt), prompt, 'returned_value')


def current_metadata() -> dict[str, Any] | None:
    attempt = _CURRENT.get()
    return attempt.metadata() if attempt is not None else None


def tracked_worker(worker):
    @wraps(worker)
    def run(item, *args, **kwargs):
        with Attempt():
            ctx = args[0] if args else kwargs.get('ctx', {})
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
