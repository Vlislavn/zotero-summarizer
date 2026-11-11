"""Shared authorization replacement is a separate effect, never registration rollback."""
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings, state, read_config, write_json_atomic, read_json_or_empty
from zotero_summarizer.services.llm import connection_state as store
from zotero_summarizer.services.llm.connection_impact import dependencies
from zotero_summarizer.services.research_feed.profile import load_profile


def credential_impact(name, selected=None):
    current = selected or settings()
    holder = state().app_state
    if holder is not None and current.project_root == settings().project_root:
        config = holder.config
    elif current.config_path.is_file():
        config = read_config(current.config_path, current.calibration_path)
    else:
        return {'providers': [], 'consumers': []}
    provider = next((p for p in config.llm_routing.providers if p.api_key_env == name and p.auth_mode != 'none'), None)
    return dependencies(config, load_profile(current.data_dir, persist_default=False), provider) if provider else {'providers': [], 'consumers': []}


def replace(command):
    """Preview without secret; explicit matching token authorizes a separate keyring write."""
    from zotero_summarizer.services.setup.doctor import store_credential_and_invalidate

    with store.editing():
        store.require_revision(command.revision)
        if command.connection_id:
            from zotero_summarizer.services.llm.connections import _owned_receipt

            _receipt, provider = _owned_receipt(command.connection_id, command.revision)
            if provider.name != command.provider:
                raise APIError('connection_conflict', 'Authorization target differs from this connection task.', status_code=409)
        else:
            provider = state().app_state.config.llm_routing.provider_by_name(command.provider)
        if provider.auth_mode == 'none':
            raise APIError('authorization_absent', 'No-auth connections do not have a credential to replace.', status_code=422)
        effects = credential_impact(provider.api_key_env)
        if not effects['providers']:
            effects = {'providers': [provider.name], 'servers': [provider.base_url or 'Native Anthropic'], 'consumers': []}
        token = store.digest([str(command.operation_id), command.revision, provider.api_key_env, effects])
        if command.secret is None:
            return {'operation_id': str(command.operation_id), 'revision': command.revision, 'preview': token, 'impact': effects}
        if command.preview != token:
            raise APIError('credential_impact_required', 'Review all shared authorization consumers before replacing.', status_code=409)
    # The Doctor owner reacquires the shared configuration lock and compares again.
    path = _receipt_path(token)
    with store.editing():
        store.require_revision(command.revision)
        if path.exists():
            raise APIError('credential_already_attempted', 'Read the authorization receipt before any new replacement.', status_code=409)
        write_json_atomic(path, {'state': 'auth_unknown', 'provider': command.provider})
    try:
        result = store_credential_and_invalidate(settings(), provider.api_key_env, command.secret,
                                                expected_revision=command.revision, impact_approved=True)
    except APIError as error:
        if error.error in {'telegram_busy', 'doctor_running', 'connection_conflict', 'configuration_busy', 'credential_impact_required', 'invalid_credential_name', 'empty_api_key'}:
            write_json_atomic(path, {'state': 'auth_refused', 'provider': command.provider, 'reason_code': error.error})
        raise
    committed_revision = result.pop('_revision')
    receipt = {'state': 'auth_stored', 'credential': result, 'revision': committed_revision, 'provider': command.provider}
    with store.editing():
        receipt['current'] = store.revision() == committed_revision
        if command.connection_id and receipt['current']:
            connection = store.load(command.connection_id)
            connection.update(revision=committed_revision, auth='stored', catalog='unavailable', models=[], preview=None)
            store.save(connection)
            store.approve(connection)
        write_json_atomic(path, receipt)
    return receipt


def _receipt_path(token):
    if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
        raise APIError('invalid_receipt', 'Invalid authorization receipt', status_code=422)
    return settings().data_dir / 'llm_connections' / ('auth-' + token + '.json')


def readback(token):
    # No secret lookup can prove a partially completed write. Preserve unknown.
    return read_json_or_empty(_receipt_path(token)) or {'state': 'not_observed'}
