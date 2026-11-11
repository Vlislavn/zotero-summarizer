"""One destination-consent owner: reuse exact, current connection task receipts."""
from uuid import UUID
import json

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.llm import connection_state as store, credential_fingerprint
from zotero_summarizer.services.llm.presets import PROVIDER_PRESET_REGISTRY, _compile_preset


def identity(provider):
    return (provider.type, provider.base_url or None, provider.api_key_env, provider.auth_mode)


def requires_approval(provider):
    if provider.is_local or provider.auth_mode == 'none':
        return False
    return identity(provider) not in {
        identity(_compile_preset(name)) for name in PROVIDER_PRESET_REGISTRY if name not in {'local', 'custom'}
    }


def _credential_current(provider, fingerprint, revision):
    """Fingerprinted approval follows this slot's key; a legacy one keeps the whole-revision check."""
    if fingerprint is None:
        store.require_revision(revision)
    elif not credential_fingerprint.matches(provider.api_key_env, fingerprint):
        raise ValueError('Credential changed')


def require_approval(provider):
    if not requires_approval(provider):
        return None
    try:
        pointer = store.approval_path(provider.name)
        fingerprint = None
        if pointer.is_file() and not pointer.is_symlink():
            recorded = json.loads(pointer.read_text())
            operation, fingerprint = UUID(recorded['operation_id']), recorded.get('credential_fingerprint')
        elif provider.name.startswith('connection_'):
            operation = UUID(provider.name.removeprefix('connection_'))
        else:
            raise ValueError('No task receipt')
        receipt = store.load(operation)
        from zotero_summarizer.models.providers import ProviderConfig

        approved = ProviderConfig.model_validate(receipt['provider'])
        actual = store.current_provider(receipt)
        if actual is None or identity(actual) != identity(provider) or identity(approved) != identity(provider):
            raise ValueError('Destination or authentication changed')
        _credential_current(provider, fingerprint, receipt['revision'])
        # Callers pin the returned revision for their request (factory._verification_key): a
        # fingerprinted approval is valid now, so pin the CURRENT state, not the receipt's.
        return receipt['revision'] if fingerprint is None else store.revision()
    except (APIError, ValueError, KeyError, TypeError, OSError):
        raise APIError('provider_approval_required',
                       'Metadata/test permission is absent or expired. In Settings → AI models, press “Load models” (or “Connect provider”) to approve this exact server.',
                       status_code=403, details={'recovery': 'open_ai_settings', 'attempted': False}) from None


def is_approved(provider):
    try:
        require_approval(provider)
        return True
    except APIError:
        return False
