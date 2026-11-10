"""Canonical register → metadata/manual ID → impact → explicitly activate task."""
from uuid import UUID

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.integrations import llm_models
from zotero_summarizer.integrations.llm_transport import validate_base
from zotero_summarizer.models import GoalsConfig
from zotero_summarizer.models.connections import RegisterConnection, ConnectionSelection
from zotero_summarizer.models.providers import ProviderConfig, STAGES
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.llm import connection_state as store
from zotero_summarizer.services.llm.connection_impact import impact, crossed_stages, correct_stages
from zotero_summarizer.services.llm.factory import resolve_api_key
from zotero_summarizer.services.research_feed.profile import load_profile


def context():
    with store.reading():
        store.require_disk_current()
        holder = state().app_state
        return {'revision': store.revision(),
                'config': holder.config.model_dump(mode='json') if holder else None,
                'providers': [p.model_dump(mode='json') for p in holder.config.llm_routing.providers] if holder else []}


def _candidate(command):
    holder = state().app_state
    routing = holder.config.llm_routing if holder else None
    source = routing.provider_by_name(command.source) if command.source and routing else None
    base = command.base_url or None
    try:
        validate_base(base or ('https://api.anthropic.com' if command.type == 'anthropic' else ''))
    except ValueError as error:
        raise APIError('invalid_api_base', str(error), status_code=422) from None
    name = 'connection_' + command.operation_id.hex
    options = source.model_dump(mode='json') if source else {}
    options.update(name=name, type=command.type, base_url=base,
                   auth_mode='none', api_key_env='CONNECTION_' + command.operation_id.hex.upper())
    if command.auth == 'existing':
        if not routing or not command.authorization:
            raise APIError('authorization_required', 'Choose an existing authorization, or explicitly choose no authorization.', status_code=422)
        authorization = routing.provider_by_name(command.authorization)
        if authorization.auth_mode == 'none':
            raise APIError('authorization_absent', 'This connection declares no authorization.', status_code=422)
        options.update(auth_mode='legacy', api_key_env=authorization.api_key_env)
    elif command.auth == 'new':
        options['auth_mode'] = 'legacy'
    if source and source.type == command.type and (source.base_url or None) == base:
        if (command.auth == 'none' and source.auth_mode == 'none'
                or command.auth == 'existing' and source.auth_mode != 'none' and source.api_key_env == options['api_key_env']):
            return source.model_copy(deep=True)
    return ProviderConfig.model_validate(options)


def register(command: RegisterConnection):
    with store.editing():
        if store.receipt_path(command.operation_id).exists():
            raise APIError('connection_already_attempted', 'Read back this registration; do not repeat an uncertain write.', status_code=409)
        store.require_revision(command.revision)
        if command.approved is not True:
            raise APIError('destination_approval_required', 'Confirm this exact protocol, API base and authorization before Connect.', status_code=403)
        try:
            provider = _candidate(command)
        except KeyError:
            raise APIError('unknown_connection_reference', 'Selected saved server/authorization is no longer available.', status_code=422) from None
        holder = state().app_state
        receipt = {'operation_id': str(command.operation_id), 'provider': provider.model_dump(mode='json'),
                   'state': 'draft', 'auth': 'absent' if provider.auth_mode == 'none' else 'unknown',
                   'catalog': 'unavailable', 'models': [], 'bootstrap': holder is None,
                   'revision': command.revision, 'source': command.source,
                   'reused': bool(holder and any(p.name == provider.name for p in holder.config.llm_routing.providers)),
                   'authorization_new': command.auth == 'new'}
        # Intent precedes the registry write: lost ACK/partial receipt write can be read back exactly.
        store.save(receipt)
        if holder:
            config = holder.config.model_copy(deep=True)
            if not receipt['reused']:
                config.llm_routing.providers.append(provider)
                store.publish(config)
            receipt['state'] = 'registered_inactive'
        receipt['revision'] = store.revision()
        store.save(receipt)
        store.approve(receipt)
        return _view(receipt)


def _view(receipt):
    return {**{key: receipt[key] for key in ('operation_id', 'provider', 'state', 'auth', 'catalog', 'models', 'bootstrap', 'revision')},
            'reused': receipt.get('reused', False), 'authorization_new': receipt.get('authorization_new', False)}


def readback(operation_id: UUID):
    with store.editing():
        receipt = store.load(operation_id)
        persisted = store.persisted_config()
        actual = next((p for p in persisted.llm_routing.providers if p.name == receipt['provider']['name']), None) if persisted else None
        provider_matches = actual is not None and actual.model_dump(mode='json') == receipt['provider']
        selection = receipt.get('selection')
        selection_matches = bool(provider_matches and selection and persisted and
                                 persisted.llm_routing.default.model_dump() == selection and
                                 all(getattr(persisted.llm_routing, stage).model_dump() == fields
                                     for stage, fields in receipt.get('stages', {}).items()))
        result = _view(receipt)
        result.update(provider_matches=provider_matches, selection_matches=selection_matches,
                      current_revision=store.revision())
        # Reconcile actual effects even if publication succeeded before receipt/ACK failed.
        if selection_matches:
            result['state'] = 'selection_saved'
        elif provider_matches:
            result['state'] = 'registered_inactive'
        return result


def _owned_receipt(operation_id, revision=None):
    receipt = store.load(operation_id)
    store.require_revision(revision or receipt['revision'])
    if receipt['revision'] != store.revision():
        raise APIError('connection_conflict', 'Reconnect and review current configuration before using this candidate.', status_code=409)
    actual = store.current_provider(receipt)
    if not receipt['bootstrap'] and (actual is None or actual.model_dump(mode='json') != receipt['provider']):
        raise APIError('connection_conflict', 'Registered provider changed; inspect saved configuration.', status_code=409)
    return receipt, ProviderConfig.model_validate(receipt['provider'])


def catalog(operation_id: UUID):
    with store.editing():
        receipt, provider = _owned_receipt(operation_id)
        if receipt['bootstrap'] and not provider.is_local:
            return {**_view(receipt), 'reason': 'First remote connection: enter a manual model ID and explicitly save a complete default before metadata discovery.'}
        revision = receipt['revision']
        try:
            key = None if provider.auth_mode == 'none' else 'local' if provider.is_local else resolve_api_key(provider)
        except APIError as error:
            return _catalog_result(operation_id, revision, error=error)

    def before_send(_request):
        with store.editing():
            store.require_revision(revision)

    try:
        # External metadata waits never own configuration publication.
        ids = (llm_models.list_openai_models(provider.base_url, key, owned=True, before_send=before_send) if provider.type == 'openai'
               else llm_models.list_anthropic_models(key, provider.base_url, owned=True, before_send=before_send))
    except (APIError, llm_models.ModelListError, ValueError) as error:
        return _catalog_result(operation_id, revision, error=error)
    return _catalog_result(operation_id, revision, ids=ids,
                           auth='absent' if key is None else 'unknown' if provider.is_local else 'stored')


def _catalog_result(operation_id, revision, *, ids=None, auth=None, error=None):
    from zotero_summarizer.services.llm.probe_failure import catalog_observation

    if isinstance(error, APIError) and error.error in {'connection_conflict', 'configuration_busy', 'config_changed_on_disk', 'provider_approval_required'}:
        raise error
    with store.editing():
        receipt, _provider = _owned_receipt(operation_id, revision)
        observation = catalog_observation(error) if error else {'reason_code': '', 'detail': '', 'http_status': None}
        receipt.update(models=[] if error else sorted(set(ids)), catalog='unavailable' if error else 'returned', **observation)
        if auth is not None:
            receipt['auth'] = auth
        store.save(receipt)
        return {**_view(receipt), **observation}


def _selected_config(provider, command):
    holder = state().app_state
    if holder:
        raw = holder.config.model_dump(mode='json')
    else:
        from zotero_summarizer.services.setup.bootstrap import connection_goals_config

        if settings().config_path.exists():
            raise APIError('bootstrap_config_required', 'Existing configuration is invalid or not loaded. Repair the complete config before saving a first default; it has not been overwritten.', status_code=409)
        if 'default' not in command.targets:
            raise APIError('bootstrap_default_required', 'Choose a default model for the first connection.', status_code=422)
        raw = connection_goals_config(provider, command.model).model_dump(mode='json')
    pair = {'provider': provider.name, 'model': command.model}
    for target in command.targets:
        raw['llm_routing'][target] = pair.copy()
    return GoalsConfig.model_validate(raw)


def preview(command: ConnectionSelection):
    with store.editing():
        receipt, provider = _owned_receipt(command.operation_id, command.revision)
        after = _selected_config(provider, command)
        profile = load_profile(settings().data_dir, persist_default=False)
        before = state().app_state.config if state().app_state else None
        required = [stage for stage in crossed_stages(before, after) if stage not in command.targets]
        if not set(command.corrections) <= set(required) or any(c not in {'previous', 'default'} for c in command.corrections.values()):
            raise APIError('invalid_route_correction', 'Only the displayed crossed routes can be corrected in this task.', status_code=422)
        after = correct_stages(before, after, command.corrections)
        effects = impact(before, after, profile, provider, source=receipt.get('source'))
        effects.update(required_corrections=required, corrections=command.corrections, targets=command.targets)
        ready = set(required) <= command.corrections.keys()
        token = store.digest([receipt['revision'], receipt['provider'], command.model, effects])
        receipt.update(preview=token, chosen_model=command.model, targets=command.targets,
                       selection=after.llm_routing.default.model_dump(),
                       stages={stage: getattr(after.llm_routing, stage).model_dump() for stage in STAGES},
                       corrections=command.corrections, ready_to_apply=ready)
        store.save(receipt)
        return {'revision': receipt['revision'], 'preview': token, 'impact': effects, 'ready_to_apply': ready}


def apply(command: ConnectionSelection):
    with store.editing():
        receipt, provider = _owned_receipt(command.operation_id, command.revision)
        if (any(c not in {'previous', 'default'} for c in command.corrections.values())
                or not command.preview or command.preview != receipt.get('preview') or not receipt.get('ready_to_apply')
                or receipt.get('corrections') != command.corrections
                or receipt.get('chosen_model') != command.model or receipt.get('targets') != command.targets):
            raise APIError('impact_required', 'Correct every crossed task to its previous complete pair or the new default, then refresh impact before using this model.', status_code=409)
        before = state().app_state.config if state().app_state else None
        store.publish(correct_stages(before, _selected_config(provider, command), command.corrections))
        receipt.update(state='selection_saved', revision=store.revision())
        store.save(receipt)
        store.approve(receipt)
        return {**_view(receipt), 'config': state().app_state.config.model_dump(mode='json'), 'selection_matches': True}
