"""Non-shipped synchronous, source-bound public-evaluation LLMClient adapter."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_OPERATION_TIMEOUT_SECONDS = 120
MAX_OPERATION_TIMEOUT_SECONDS = 600
MAX_LOGICAL_CALLS = 10_000
OPERATION_TIMEOUT_ENV = 'ZS_METHOD_OPERATION_TIMEOUT_SECONDS'
ENDPOINT = 'https://chatgpt.com/backend-api/codex/responses'
USAGE_FIELDS = ('input_tokens', 'output_tokens', 'total_tokens', 'cache_read_tokens',
                'reasoning_tokens', 'cache_creation_input_tokens')


class ClientError(RuntimeError):
    """Safe diagnostics: never include credentials or opaque provider exceptions."""


@dataclass(frozen=True)
class ClientOptions:
    model: str
    reasoning_effort: str
    session_id: str
    max_logical_calls: int
    source_path: Path
    source_sha256: str
    operation_timeout_seconds: int = field(default_factory=lambda: int(
        os.environ.get(OPERATION_TIMEOUT_ENV, DEFAULT_OPERATION_TIMEOUT_SECONDS)))

    def __post_init__(self) -> None:
        for name in ('model', 'reasoning_effort', 'session_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f'{name} must be a nonempty, trimmed string')
        if self.model == 'gpt-6.1-sol' and self.reasoning_effort != 'xhigh':
            raise ValueError('gpt-6.1-sol requires xhigh')
        for name, maximum in (('max_logical_calls', MAX_LOGICAL_CALLS),
                              ('operation_timeout_seconds', MAX_OPERATION_TIMEOUT_SECONDS)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f'{name} must be an integer in 1..{maximum}')
        if not isinstance(self.source_path, Path) or not self.source_path.is_absolute():
            raise ValueError('source_path must be an absolute Path')
        if (not isinstance(self.source_sha256, str) or len(self.source_sha256) != 64
                or any(char not in '0123456789abcdef' for char in self.source_sha256)):
            raise ValueError('source_sha256 must be the full lowercase SHA-256')


def _parse_output(parser: Any, content: str) -> tuple[Any, str]:
    from langchain_core.exceptions import OutputParserException
    from onprem.llm.helpers import extract_json

    try:
        return parser.parse(content), 'success'
    except OutputParserException:
        extracted = extract_json(content)
        try:
            return parser.parse(extracted), 'success'
        except OutputParserException:
            return content, 'parse_failure'


def _usage(message: Any) -> tuple[dict[str, int | None], str]:
    raw = message.usage_metadata or {}
    input_details = raw.get('input_token_details') or {}
    output_details = raw.get('output_token_details') or {}
    values = [raw.get('input_tokens'), raw.get('output_tokens'), raw.get('total_tokens'),
              input_details.get('cache_read'), output_details.get('reasoning'),
              input_details.get('cache_creation')]
    invalid = any(value is not None and (type(value) is not int or value < 0) for value in values)
    usage = {key: value if type(value) is int and value >= 0 else None
             for key, value in zip(USAGE_FIELDS, values)}
    totals = [usage[key] for key in USAGE_FIELDS[:3]]
    if all(value is not None for value in totals) and totals[0] + totals[1] != totals[2]:
        invalid = True
    status = 'unavailable' if not raw else ('provider_reported_partial' if None in values else 'provider_reported')
    if invalid:
        status = 'invalid'
    return usage, status


@contextmanager
def _without_remote_tracing() -> Iterator[None]:
    from langchain_core.tracers.context import tracing_v2_callback_var
    from langsmith.run_helpers import tracing_context

    token = tracing_v2_callback_var.set(None)
    try:
        with tracing_context(enabled=False, parent=False):
            yield
    finally:
        tracing_v2_callback_var.reset(token)


class _DisabledAsync:
    def __getattr__(self, name: str) -> Any:
        raise ClientError('method-validation client is synchronous only')


class MethodValidationClient:
    def __init__(self, options: ClientOptions, *, observer: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.options = options
        self.observer = observer
        self.logical_calls = 0
        self._closed = False
        self._model = None
        self._http = None
        self._active: dict[str, Any] | None = None
        source = self._check_source()
        name = f'_method_transport_{options.source_sha256}'
        module = types.ModuleType(name)
        module.__file__ = str(options.source_path)
        sys.modules[name] = module
        try:
            exec(compile(source, str(options.source_path), 'exec'), module.__dict__)
        except Exception:
            del sys.modules[name]
            raise ClientError('source dependency could not be loaded') from None
        self._source = module

    def _check_source(self) -> bytes:
        try:
            source = self.options.source_path.read_bytes()
        except OSError:
            raise ClientError('source dependency unavailable') from None
        if hashlib.sha256(source).hexdigest() != self.options.source_sha256:
            raise ClientError('source dependency SHA-256 mismatch')
        return source

    def _auth(self) -> Any:
        import httpx

        source = self._source

        class ExactDestinationAuth(source.PiCodexAuth):
            def _authenticate(self, request: httpx.Request) -> httpx.Request:
                if str(request.url) != ENDPOINT:
                    raise ClientError('refusing credential resolution for forbidden destination')
                return super()._authenticate(request)

        return ExactDestinationAuth(source.PiCodexTokenProvider(self.options.model))

    def _observe_request(self, request: Any) -> None:
        payload = json.loads(request.content)
        safe = self._request_fields(payload, 'httpx_request_hook_pre_dispatch')
        self._active['requests'].append(safe)

    def _request_fields(self, payload: dict[str, Any], scope: str) -> dict[str, Any]:
        safe = {'scope': scope, 'model': payload.get('model'),
                'effort': (payload.get('reasoning') or {}).get('effort'),
                'session_id': payload.get('prompt_cache_key'),
                'store': payload.get('store'), 'stream': payload.get('stream')}
        if (safe['model'] != self.options.model or safe['effort'] != self.options.reasoning_effort
                or safe['session_id'] != self.options.session_id
                or safe['store'] is not False or safe['stream'] is not True):
            raise ClientError('request model/effort/session/stream contract mismatch')
        return safe

    def _build_model(self) -> Any:
        import httpx
        import openai
        from langchain_openai import ChatOpenAI

        owner = self

        class NeutralChatOpenAI(ChatOpenAI):
            async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
                raise ClientError('method-validation client is synchronous only')

            def _astream(self, *args: Any, **kwargs: Any) -> Any:
                raise ClientError('method-validation client is synchronous only')

            def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> dict[str, Any]:
                payload = owner._source.normalize_codex_payload(
                    super()._get_request_payload(input_, stop=stop, **kwargs),
                    reasoning_effort=owner.options.reasoning_effort, session_id=owner.options.session_id,
                )
                owner._active['generated_request'] = owner._request_fields(payload, 'generated_payload')
                return payload

        self._http = httpx.Client(auth=self._auth(), trust_env=False, follow_redirects=False,
                                  timeout=self.options.operation_timeout_seconds,
                                  event_hooks={'request': [self._observe_request]})
        try:
            root = openai.OpenAI(api_key='pi-oauth-managed', base_url=self._source.CODEX_BASE_URL,
                                 max_retries=0, timeout=self.options.operation_timeout_seconds,
                                 http_client=self._http, organization='', project='')
            disabled = _DisabledAsync()
            return NeutralChatOpenAI(
                model=self.options.model, api_key='pi-oauth-managed', base_url=self._source.CODEX_BASE_URL,
                root_client=root, client=root.chat.completions, async_client=disabled, root_async_client=disabled,
                streaming=True, use_responses_api=True, output_version='v0', max_retries=0,
                cache=False, callbacks=[],
                request_timeout=self.options.operation_timeout_seconds,
            )
        except Exception:
            self._http.close()
            raise ClientError('synchronous client construction failed') from None

    @staticmethod
    def _validate_kwargs(kwargs: dict[str, Any]) -> None:
        if set(kwargs) - {'stop'} or ('stop' in kwargs and kwargs['stop'] is not None and kwargs['stop'] != []):
            raise ValueError('unsupported request semantics; only empty stop is accepted')

    def prompt(self, prompt: str, **kwargs: Any) -> str:
        self._validate_kwargs(kwargs)
        with _without_remote_tracing():
            return self._complete(prompt, prompt)

    def pydantic_prompt(self, prompt: str, pydantic_model: Any, **kwargs: Any) -> Any:
        self._validate_kwargs(kwargs)
        with _without_remote_tracing():
            from onprem.llm.base import LLM

            formatted, parser = LLM._format_pydantic_prompt(None, prompt, pydantic_model)
            return self._complete(prompt, formatted, parser)

    def _emit(self, receipt: dict[str, Any]) -> None:
        if self.observer is not None:
            self.observer(copy.deepcopy(receipt))

    def _complete(self, original: str, request_prompt: str, parser: Any = None) -> Any:
        if not isinstance(original, str) or not isinstance(request_prompt, str):
            raise ValueError('prompt must be text')
        if self._closed:
            raise ClientError('client is closed')
        if self._active is not None:
            raise ClientError('concurrent or reentrant calls are unsupported')
        if self.logical_calls >= self.options.max_logical_calls:
            raise ClientError('logical call budget exhausted')
        self._check_source()
        self.logical_calls += 1
        receipt = {
            'event': 'call_terminal', 'logical_call': self.logical_calls,
            'configured_model': self.options.model, 'configured_effort': self.options.reasoning_effort,
            'session_id': self.options.session_id, 'source_sha256': self.options.source_sha256,
            'original_prompt': original, 'request_prompt': request_prompt, 'returned_content': None,
            'observed_model': None, 'generated_request': None, 'requests': [], 'attempts': [],
            'sdk_retries': 0, 'stream_retries': 0,
            'operation_timeout_seconds': self.options.operation_timeout_seconds, 'external_wall_guard_required': True,
            'usage': {key: None for key in USAGE_FIELDS}, 'usage_status': 'unavailable',
        }
        self._active = receipt
        try:
            self._emit({**receipt, 'event': 'call_started', 'status': 'started'})
            try:
                if self._model is None:
                    self._model = self._build_model()
                message = self._model.invoke(request_prompt, config={'callbacks': []})
            except Exception as exc:
                receipt.update(status='transport_failure', error_class=type(exc).__name__,
                               attempts=[{'attempt': 1, 'status': 'terminal_failure',
                                          'http_request_count': len(receipt['requests'])}])
                self._emit(receipt)
                raise ClientError('logical call failed; see safe terminal receipt') from None
            receipt['attempts'] = [{'attempt': 1, 'status': 'response_received',
                                    'http_request_count': len(receipt['requests'])}]
            return self._accept_response(message, parser, receipt)
        finally:
            self._active = None

    def _accept_response(self, message: Any, parser: Any, receipt: dict[str, Any]) -> Any:
        metadata = message.response_metadata or {}
        content = message.content
        receipt['returned_content'] = content if isinstance(content, str) else self._source._response_text(content)
        receipt['observed_model'] = metadata.get('model_name') or metadata.get('model')
        receipt['usage'], receipt['usage_status'] = _usage(message)
        receipt['provider_status'] = metadata.get('status')
        receipt['finish_reason'] = metadata.get('finish_reason')
        failure = None
        if receipt['observed_model'] != self.options.model:
            failure = 'model_mismatch'
        elif receipt['usage_status'] == 'invalid':
            failure = 'invalid_usage'
        elif receipt['returned_content'] is None:
            failure = 'unsupported_content'
        elif receipt['provider_status'] not in (None, 'completed') or receipt['finish_reason'] == 'length':
            failure = 'incomplete_response'
        if failure:
            receipt['status'] = failure
            self._emit(receipt)
            if failure == 'incomplete_response' and receipt['finish_reason'] == 'length':
                from zotero_summarizer.integrations.llm_callbacks import LLMOutputTruncated

                raise LLMOutputTruncated('provider reported length; see safe terminal receipt')
            raise ClientError(f'{failure}; see safe terminal receipt')
        result = receipt['returned_content']
        receipt['status'] = 'success'
        if parser is not None:
            result, receipt['status'] = _parse_output(parser, result)
        self._emit(receipt)
        return result

    def close(self) -> None:
        if self._active is not None:
            raise ClientError('cannot close during an active call')
        self._closed = True
        if self._http is not None:
            self._http.close()

    def __enter__(self) -> MethodValidationClient:
        if self._closed:
            raise ClientError('client is closed')
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
