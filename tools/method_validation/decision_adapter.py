"""One source-bound llama.cpp b11430 text-choice boundary; not model qualification."""
from __future__ import annotations

import json
from dataclasses import dataclass
from math import fsum, isfinite
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import TypeAdapter, ValidationError

from tools.method_validation.support import Probability, ProviderReply, SupportArm

_VECTOR = TypeAdapter(tuple[Probability, ...])
_PROBABILITY = TypeAdapter(Probability)
_INSTRUCTIONS = 'Answer the decision in the state using only the supplied options.'


class DecisionAdapterError(RuntimeError):
    """Safe protocol/admission error; never a scientific or training negative."""


@dataclass(frozen=True)
class DecisionAdapterOptions:
    endpoint: str
    max_logical_calls: int
    timeout_seconds: float
    max_request_bytes: int
    max_response_bytes: int

    def __post_init__(self) -> None:
        if (not isinstance(self.endpoint, str)
                or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in self.endpoint)):
            raise ValueError('endpoint must be an explicit HTTP(S) systemone URL')
        try:
            httpx.URL(self.endpoint)
            url = urlsplit(self.endpoint)
            valid = (url.scheme in ('http', 'https') and url.hostname and url.port != 0
                     and url.path == '/v1/systemone' and url.username is None
                     and url.password is None and '?' not in self.endpoint and '#' not in self.endpoint)
        except (ValueError, httpx.InvalidURL):
            valid = False
        if not valid:
            raise ValueError('endpoint must be an explicit HTTP(S) systemone URL without credentials')
        for name in ('max_logical_calls', 'max_request_bytes', 'max_response_bytes'):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f'{name} must be an explicit positive integer')
        if (type(self.timeout_seconds) not in (int, float)
                or not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError('timeout_seconds must be explicit, positive and finite')


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DecisionAdapterError('duplicate JSON member in native response')
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise DecisionAdapterError('nonfinite JSON constant in native response')


def _fields(value: Any, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise DecisionAdapterError('unsupported native response schema')
    return value


def _readout(raw: str, options: tuple[str, ...], arm: SupportArm) -> ProviderReply:
    try:
        data = json.loads(raw, object_pairs_hook=_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise DecisionAdapterError('invalid native response JSON') from None
    data = _fields(data, {'model', 'answers', 'usage'})
    model = data['model']
    if not isinstance(model, str) or not model.strip() or model != arm.model:
        raise DecisionAdapterError('returned model missing or different from frozen arm')
    answers = _fields(data['answers'], {'decision'})
    answer = _fields(answers['decision'], {'type', 'choice', 'probabilities', 'confidence'})
    if (answer['type'] != 'choice' or not isinstance(answer['choice'], str)
            or answer['choice'] not in options):
        raise DecisionAdapterError('native choice must be an exact declared option')
    probabilities = _fields(answer['probabilities'], set(options))
    try:
        vector = _VECTOR.validate_python(tuple(probabilities[option] for option in options))
        _PROBABILITY.validate_python(answer['confidence'])
    except ValidationError:
        raise DecisionAdapterError('invalid native probability') from None
    if abs(fsum(vector) - 1) > arm.probability_sum_tolerance:
        raise DecisionAdapterError('native vector does not sum to one within frozen tolerance')
    usage = _fields(data['usage'], {'input_tokens', 'output_tokens'})
    if (type(usage['input_tokens']) is not int or usage['input_tokens'] < 0
            or type(usage['output_tokens']) is not int or usage['output_tokens'] != 0):
        raise DecisionAdapterError('invalid native usage schema')
    return ProviderReply(raw_response=raw, readout={
        'label': answer['choice'], 'probabilities': vector, 'observed_model': model,
    })


class NativeDecisionAdapter:
    """Serial callable using frozen SupportArm and one explicit caller-owned URL.

    No client/network/auth/environment access at construction. Each explicit call
    owns one HTTP client and one POST; no cookies survive, no redirects or retries.
    The idle timeout is NOT a hard deadline or evidence of token/context fit.
    An injected transport is trusted code (MockTransport in source controls), not
    an untrusted extension point or proof about retries inside that transport.
    """

    def __init__(self, arm: SupportArm, options: DecisionAdapterOptions, *,
                 transport: httpx.BaseTransport | None = None) -> None:
        if not isinstance(arm, SupportArm) or not isinstance(options, DecisionAdapterOptions):
            raise TypeError('a frozen SupportArm and DecisionAdapterOptions are required')
        self._arm = arm
        self._options = options
        self._transport = transport
        self._logical_calls = 0
        self._lock = Lock()

    @property
    def logical_calls(self) -> int:
        return self._logical_calls

    def _payload(self, prompt: str, options: tuple[str, ...]) -> bytes:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('prompt must be nonblank text')
        if len(prompt) > self._arm.max_prompt_chars:
            raise ValueError('prompt exceeds frozen character cap; no clipping')
        if (type(options) is not tuple or not 2 <= len(options) <= 255
                or any(not isinstance(option, str) or not option.strip() for option in options)
                or len(set(options)) != len(options)):
            raise ValueError('options must be 2..255 unique exact nonblank strings')
        if len(prompt) + sum(len(option) for option in options) > self._options.max_request_bytes:
            raise ValueError('request exceeds frozen byte cap')
        try:
            payload = json.dumps({
                'state': prompt,
                'questions': {'decision': {'type': 'choice', 'instructions': _INSTRUCTIONS,
                                          'criteria': {option: None for option in options}}},
            }, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('prompt and options must encode as UTF-8') from None
        if len(payload) > self._options.max_request_bytes:
            raise ValueError('request exceeds frozen byte cap')
        return payload

    def __call__(self, prompt: str, options: tuple[str, ...]) -> ProviderReply:
        if not self._lock.acquire(blocking=False):
            raise DecisionAdapterError('concurrent or reentrant native call refused')
        try:
            payload = self._payload(prompt, options)
            if self._logical_calls >= self._options.max_logical_calls:
                raise DecisionAdapterError('native logical call budget exhausted')
            self._logical_calls += 1
            return _readout(self._post(payload), options, self._arm)
        finally:
            self._lock.release()

    def _post(self, payload: bytes) -> str:
        transport = self._transport
        if transport is None:
            transport = httpx.HTTPTransport(retries=0, trust_env=False)
        try:
            with httpx.Client(transport=transport, auth=None, trust_env=False,
                              follow_redirects=False, timeout=self._options.timeout_seconds) as client:
                with client.stream('POST', self._options.endpoint, content=payload, headers={
                    'Content-Type': 'application/json', 'Accept': 'application/json',
                    'Accept-Encoding': 'identity',
                }) as response:
                    if response.status_code != 200:
                        raise ConnectionError('native HTTP request unavailable; no retry')
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise DecisionAdapterError('encoded native response unsupported')
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=min(self._options.max_response_bytes + 1, 65536)):
                        if len(body) + len(chunk) > self._options.max_response_bytes:
                            raise DecisionAdapterError('native response exceeds frozen byte cap')
                        body.extend(chunk)
            return body.decode('utf-8')
        except httpx.TimeoutException:
            raise TimeoutError('native HTTP timeout; no retry') from None
        except httpx.RequestError:
            raise ConnectionError('native HTTP transport unavailable; no retry') from None
        except UnicodeDecodeError:
            raise DecisionAdapterError('native response must be UTF-8 JSON') from None
