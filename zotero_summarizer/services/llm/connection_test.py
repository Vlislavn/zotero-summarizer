"""Explicit saved-model/task probes reuse Setup attempt and native request ownership."""
import asyncio
from functools import partial

from zotero_summarizer.models.providers import resolve_stage
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import state
from zotero_summarizer.services.llm import connections, connection_state as store, connection_approval, operational_check
from zotero_summarizer.services.setup.load_attempt import SetupAttempt


def test_connection(command):
    with store.editing():
        store.require_revision(command.revision)
        config = state().app_state.config if state().app_state else None
        if not config or config.llm_routing.default.model != command.model:
            raise APIError('selection_required', 'Explicitly save this default model before testing it.', status_code=409)
        provider = config.llm_routing.provider_by_name(config.llm_routing.default.provider).model_copy(deep=True)
        if command.operation_id:
            receipt, provider = connections._owned_receipt(command.operation_id, command.revision)
            if receipt.get('selection') != config.llm_routing.default.model_dump():
                raise APIError('selection_required', 'This task has not saved the current default.', status_code=409)
        base = {'revision': command.revision, 'provider': provider.name, 'model': command.model,
                'base_url': provider.base_url, 'current': True}
        if not config.llm_enabled:
            return {**base, 'status': 'disabled', 'attempted': False,
                    'detail': 'AI is off. Save Enable AI-assisted features before requesting a model test.'}
        try:
            connection_approval.require_approval(provider)
        except APIError as error:
            return {**base, 'status': 'needs_approval', 'attempted': False, 'reason_code': error.error,
                    'detail': error.message, 'recovery': 'open_ai_settings'}

    def require_current():
        # Short credential/pre-send admission, never network/model completion.
        with store.editing():
            store.require_revision(command.revision)

    attempt = SetupAttempt(before_request=require_current)
    result = attempt.run(lambda: operational_check.probe_provider(provider, command.model, memory_probe=True),
                         timeout=operational_check.probe_timeout(provider))
    return {**result, 'revision': command.revision, 'current': store.revision() == command.revision,
            'attempt': attempt.receipt(), 'provider': provider.name, 'model': command.model,
            'base_url': provider.base_url}


def _require_current(revision):
    with store.editing():
        store.require_revision(revision)


def test_tasks(command):
    with store.editing():
        store.require_revision(command.revision)
        config = state().app_state.config if state().app_state else None
        if config is None:
            raise APIError('selection_required', 'Save task model assignments before testing them.', status_code=409)
        routing = config.llm_routing.model_copy(deep=True)
        selected = [resolve_stage(routing, stage) for stage in command.stages]
        base = {'revision': command.revision, 'targets': command.stages, 'current': True,
                'selections': {row.stage: {'provider': row.provider.name, 'model': row.model,
                                          'base_url': row.provider.base_url} for row in selected}}
        if not config.llm_enabled:
            return {**base, 'status': 'disabled', 'attempted': False,
                    'stages': [{'stage': row.stage, 'provider': row.provider.name, 'model': row.model,
                                'status': 'disabled', 'attempted': False} for row in selected],
                    'detail': 'AI-assisted features are off. No model was tested.'}
    unique = {(row.provider.name, row.model): row.provider for row in selected}
    attempt = SetupAttempt(before_request=partial(_require_current, command.revision))
    result = attempt.run(lambda: asyncio.run(operational_check.check_routing_stages(
        routing, should_stop=attempt.is_abandoned, stages=command.stages, memory_probe=True)),
        timeout=sum(operational_check.probe_timeout(provider) for provider in unique.values()))
    return {**base, **result, 'current': store.revision() == command.revision, 'attempt': attempt.receipt()}

