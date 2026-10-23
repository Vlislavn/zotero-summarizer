"""Non-shipped, single-case public RSS rubric diagnostic; not a daemon runner."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from tools.method_validation.client import ClientOptions, MethodValidationClient
from zotero_summarizer import runtime
from zotero_summarizer.integrations.app_rss import _entry_to_item
from zotero_summarizer.models import AppState, GoalsConfig, SummarizeRequest, SummarizeResponse
from zotero_summarizer.services.triage.feeds._triage import _triage_one
from zotero_summarizer.services.triage.prompts import DEFAULT_TRIAGE_PROMPT
from zotero_summarizer.services.triage.summarization import run_abstract_pipeline
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_item

CONTRACT_ENV = 'ZS_PRACTITIONER_CONTRACT'
_CONTEXT_LOCK = Lock()


class _FrozenProjection(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class _Binding(_FrozenProjection):
    path: Path
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @field_validator('path')
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError('binding path must be absolute')
        return value


class _DescriptionBinding(_Binding):
    bytes: StrictInt = Field(ge=0)


class _Provenance(_Binding):
    xml_locator: str


class _RawCase(_FrozenProjection):
    case_id: str = Field(pattern=r'^[a-z0-9-]+$')
    title: str
    doi: str | None
    guid: str
    url: str
    input_mode: Literal['rss_description']
    abstract: str
    abstract_binding: _DescriptionBinding
    stable_feed_key_derivation: str
    provenance: _Provenance


class _Cohort(_FrozenProjection):
    schema_version: Literal[1]
    cases: tuple[_RawCase, ...]


class GeneratorCase(_FrozenProjection):
    """Only parser-confirmed generator fields; no grader source/path handles."""

    case_id: str
    title: str = Field(min_length=1)
    doi: str | None
    guid: str
    url: str
    abstract: str
    stable_feed_key: str
    xml_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    cohort_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    description_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    parser_version: str

    def request(self) -> SummarizeRequest:
        return SummarizeRequest(title=self.title, doi=self.doi, abstract=self.abstract, pdf_path='')


class _GoalIntent(_FrozenProjection):
    research_goals: tuple[str, ...]
    triage_criteria: tuple[str, ...]
    summary_structure: tuple[str, ...]
    relevance_scale: dict[int, str]
    reading_priority_scale: dict[str, str]
    output_language: str = Field(min_length=1)


class DiagnosticContract(_FrozenProjection):
    """One explicit env-overridable contract, frozen before outputs; judge HOLD."""

    case_id: str
    rubric: Literal['production', 'academic']
    model: Literal['gpt-6.1-sol']
    effort: Literal['xhigh']
    session_id: str = Field(min_length=1)
    max_logical_calls: StrictInt = Field(ge=1, le=3)
    operation_timeout_seconds: StrictInt = Field(ge=1, le=120)
    external_wall_cap_seconds: StrictInt = Field(ge=60, le=600)
    semantic_judge_calls: StrictInt = Field(ge=0, le=0)
    transport_path: Path
    transport_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    goals: _GoalIntent

    @model_validator(mode='after')
    def validate_envelope(self) -> DiagnosticContract:
        self.product_config()
        ClientOptions(model=self.model, reasoning_effort=self.effort, session_id=self.session_id,
                      max_logical_calls=self.max_logical_calls, source_path=self.transport_path,
                      source_sha256=self.transport_sha256,
                      operation_timeout_seconds=self.operation_timeout_seconds)
        return self

    @classmethod
    def from_env(cls) -> DiagnosticContract:
        return cls.model_validate_json(os.environ[CONTRACT_ENV])

    def product_config(self) -> GoalsConfig:
        # Required legacy connection metadata is never used: every call gets the
        # explicit existing diagnostic client, not the product provider factory.
        return GoalsConfig.model_validate({**self.goals.model_dump(mode='json'), 'llm': {
            'draft_model': self.model, 'refine_model': self.model,
            'api_base': 'https://chatgpt.com/backend-api/codex',
            'api_key_env': 'UNUSED_DIAGNOSTIC_CLIENT',
        }})


def _bound_bytes(path: Path, expected: str) -> bytes:
    if not path.is_absolute():
        raise ValueError('input path must be absolute')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('input SHA-256 mismatch')
    return data


def load_generator_case(
    cohort_path: Path, cohort_sha256: str, xml_path: Path, xml_sha256: str, case_id: str,
) -> GeneratorCase:
    """Admit generator manifest/description only; map actual installed parser output."""
    import feedparser

    cohort = _Cohort.model_validate_json(_bound_bytes(cohort_path, cohort_sha256))
    selected = [c for c in cohort.cases if c.case_id == case_id]
    if len(selected) != 1:
        raise ValueError('case_id must resolve exactly one generator case')
    case = selected[0]
    binding = case.abstract_binding
    if binding.path != cohort_path.parent / f'{case_id}.rss-description.txt':
        raise ValueError('description must be a sibling generator input')
    description = _bound_bytes(binding.path, binding.sha256)
    if len(description) != binding.bytes or description.decode() != case.abstract:
        raise ValueError('description binding mismatch')
    if case.provenance.path != xml_path or case.provenance.sha256 != xml_sha256:
        raise ValueError('XML provenance mismatch')
    parsed = feedparser.parse(_bound_bytes(xml_path, xml_sha256))
    if parsed.bozo or not parsed.version:
        raise ValueError('invalid captured RSS')
    entries = [e for e in parsed.entries if e.get('id') == case.guid and e.get('link') == case.url]
    if len(entries) != 1:
        raise ValueError('GUID/URL must resolve exactly one captured entry')
    item = _entry_to_item(entries[0], feed={'id': 1, 'name': 'DIAGNOSTIC capture'},
                          feed_title=str(parsed.feed.get('title') or ''))
    if (item['title'] != case.title or item['abstract'] != case.abstract
            or (item['doi'] or None) != case.doi
            or item['stable_feed_key'] != case.stable_feed_key_derivation):
        raise ValueError('actual parser projection differs from frozen generator input')
    return GeneratorCase(case_id=case_id, title=item['title'], doi=item['doi'] or None,
                         guid=item['guid'], url=item['url'], abstract=item['abstract'],
                         stable_feed_key=item['stable_feed_key'], xml_sha256=xml_sha256,
                         cohort_sha256=cohort_sha256, description_sha256=binding.sha256,
                         parser_version=feedparser.__version__)


def _clean_settings(root: Path, wall_cap: int) -> Settings:
    """Construct owned paths directly: never Settings.load/.env/HOME/calibration."""
    data = root / 'data'
    return Settings(
        project_root=root, data_dir=data, config_path=root / 'config.json',
        calibration_path=data / 'unused-calibration.json', env_path=root / 'absent.env',
        summary_timeout_seconds=wall_cap, triage_job_concurrency=1,
        pdf_root=data / 'pdfs', zotero_data_dir=data / 'no-zotero',
        app_log_level='INFO', app_log_file=data / 'server.log',
        triage_db_path=data / 'triage.db', corpus_db_path=data / 'corpus.db',
        model_dir=data / 'models', tuned_params_path=data / 'unused-params.json',
        pdf_cache_dir=data / 'pdfs', golden_csv_path=data / 'unused-golden.csv',
        golden_jsonl_path=data / 'unused-golden.jsonl', interaction_log_path=data / 'events.jsonl',
        faithbench_dir=data / 'unused-faithbench', paper_render_dir=data / 'unused-render',
        search_dir=data / 'unused-search', browser_profile_dir=data / 'unused-browser',
    )


def _save(path: Path, value: Any) -> None:
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def _source_bindings() -> dict[str, str]:
    from tools.method_validation import client
    from zotero_summarizer.services.triage import prompts, summarization
    from zotero_summarizer.services.triage.feeds import _triage

    paths = [Path(module.__file__) for module in (client, prompts, summarization, _triage)]
    paths.append(Path(__file__))
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _score_case(contract: DiagnosticContract, case: GeneratorCase, client: MethodValidationClient):
    if contract.rubric == 'academic':
        return run_abstract_pipeline(case.request(), llm_override=client,
                                     triage_prompt_override=DEFAULT_TRIAGE_PROMPT)
    candidate, _, _ = _triage_one(case.model_dump(), log_prefix='practitioner-diagnostic',
                                          triage_llm=client)
    if candidate is None:
        # The feed boundary catches exceptions. Do not persist its raw message.
        raise RuntimeError('production feed primitive failed')
    return candidate.summary


def _outcome(summary: SummarizeResponse | None, events: list[dict]) -> str:
    if summary is None:
        terminal = [e for e in events if e['event'] == 'call_terminal']
        if not terminal:
            return 'nonattempt_setup_failure'
        if terminal[-1]['status'] != 'success':
            return terminal[-1]['status']
        return 'pipeline_failure'
    # Generated tags are data; only zero-call paths can signal machine gates.
    if any(e['event'] == 'call_started' for e in events):
        return 'diagnostic_completed'
    if 'prefilter_low_corpus_affinity' in summary.tags:
        return 'gate_censored'
    if 'abstract_missing' in summary.tags:
        return 'nonattempt_missing_abstract'
    return 'diagnostic_completed'


def run_diagnostic(
    contract: DiagnosticContract, case: GeneratorCase, output_root: Path, *, observer=None,
) -> dict[str, Any]:
    """One serial attempt on a new consented owned root; external guard mandatory.

    No cohort loop, selection, processed-feed write, browser, judge or registration.
    A returned receipt does not establish meaningful-content acceptance.
    """
    # Detach nested maps before any callback: frozen pydantic objects are shallow.
    contract = DiagnosticContract.model_validate_json(contract.model_dump_json())
    case = GeneratorCase.model_validate_json(case.model_dump_json())
    if contract.case_id != case.case_id:
        raise ValueError('contract case_id mismatch')
    if stable_feed_key_from_item(case.model_dump()) != case.stable_feed_key:
        raise ValueError('stable identity mismatch')
    if not output_root.is_absolute():
        raise ValueError('output_root must be absolute')
    if not _CONTEXT_LOCK.acquire(blocking=False):
        raise RuntimeError('concurrent practitioner diagnostics unsupported')
    previous = runtime._context
    try:
        output_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        config = contract.product_config()
        settings = _clean_settings(output_root, contract.external_wall_cap_seconds)
        settings.data_dir.mkdir(mode=0o700)
        _save(output_root / 'contract.json', contract.model_dump(mode='json'))
        _save(settings.config_path, config.model_dump(mode='json'))
        _save(output_root / 'input.json', case.model_dump(mode='json'))
        return _execute(contract, case, settings, config, observer)
    finally:
        runtime._context = previous
        _CONTEXT_LOCK.release()


def _execute(contract, case, settings, config, observer) -> dict[str, Any]:
    root = settings.project_root
    events: list[dict[str, Any]] = []

    def observe(event):
        events.append(copy.deepcopy(event))
        # Retain each checkpoint without overwriting previous call evidence.
        _save(root / f"call-{len(events):02d}.json", event)
        if observer is not None:
            observer(copy.deepcopy(event))

    bindings = _source_bindings()
    receipt = {'schema_version': 1, 'scope': 'single paired-rubric diagnostic arm, not daemon/selection',
               'rubric': contract.rubric, 'input': case.model_dump(mode='json'),
               'corpus': {'enabled': config.corpus.enabled, 'cache_available': False},
               'source_bindings': bindings, 'semantic_judge_calls': 0,
               'processed_feed_persistence': False, 'external_guard_required': True,
               'max_logical_calls': contract.max_logical_calls,
               'external_wall_cap_seconds': contract.external_wall_cap_seconds,
               'native_output_cap': None, 'native_response_format': None}
    summary = None
    client = None
    try:
        options = ClientOptions(model=contract.model, reasoning_effort=contract.effort,
                                session_id=contract.session_id, max_logical_calls=contract.max_logical_calls,
                                source_path=contract.transport_path, source_sha256=contract.transport_sha256,
                                operation_timeout_seconds=contract.operation_timeout_seconds)
        with MethodValidationClient(options, observer=observe) as client:
            runtime.set_context(runtime.AppContext(settings=settings, state=runtime.RuntimeState(
                app_state=AppState(config=config))))
            summary = _score_case(contract, case, client)
            _save(root / 'response.json', summary.model_dump(mode='json'))
            reloaded = SummarizeResponse.model_validate_json((root / 'response.json').read_text())
            if reloaded.model_dump(mode='json') != summary.model_dump(mode='json'):
                raise ValueError('diagnostic response reopen differs')
    except Exception as exc:
        receipt['error_class'] = type(exc).__name__
        summary = None
    terminal = [e for e in events if e['event'] == 'call_terminal']
    receipt.update(status=_outcome(summary, events), logical_calls=client.logical_calls if client else 0,
                   actual_http_request_count=sum(len(e['requests']) for e in terminal))
    _save(root / 'events.json', events)
    if _source_bindings() != bindings:
        receipt['status'] = 'source_drift'
    _save(root / 'receipt.json', receipt)
    return receipt
