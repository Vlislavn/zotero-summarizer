"""One destination-consent owner: reuse exact, current connection task receipts."""
from uuid import UUID
import json

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.llm import connection_state as store
from zotero_summarizer.services.llm.presets import PROVIDER_PRESET_REGISTRY, _compile_preset


def identity(provider):
    return (provider.type, provider.base_url or None, provider.api_key_env, provider.auth_mode)


def requires_approval(provider):
    if provider.is_local or provider.auth_mode == 'none':
        return False
    return identity(provider) not in {
        identity(_compile_preset(name)) for name in PROVIDER_PRESET_REGISTRY if name not in {'local', 'custom'}
    }


def require_approval(provider):
    if not requires_approval(provider):
        return None
    try:
        pointer = store.approval_path(provider.name)
        if pointer.is_file() and not pointer.is_symlink():
            operation = UUID(json.loads(pointer.read_text())['operation_id'])
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
        store.require_revision(receipt['revision'])
        return receipt['revision']
    except (APIError, ValueError, KeyError, TypeError, OSError):
        raise APIError('provider_approval_required',
                       'Metadata/test permission is absent or expired. Open AI connection settings and approve this exact server and authorization.',
                       status_code=403, details={'recovery': 'open_ai_settings', 'attempted': False}) from None


def is_approved(provider):
    try:
        require_approval(provider)
        return True
    except APIError:
        return False
