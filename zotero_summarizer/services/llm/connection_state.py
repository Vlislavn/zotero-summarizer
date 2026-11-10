"""Connection receipts and public revision boundary, not a second provider registry."""
from contextlib import contextmanager
import hashlib
import json
from uuid import UUID

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models import AppState, GoalsConfig
from zotero_summarizer.services._common import settings, state, read_config, write_json_atomic, write_user_config
from zotero_summarizer.storage.configuration_lock import configuration_lock, file_revision


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def revision() -> str:
    selected = settings()
    holder = state().app_state
    paths = [selected.config_path, selected.calibration_path,
             selected.data_dir / 'setup_credential_revision.json',
             selected.data_dir / 'research_feed' / 'profile.json']
    config = holder.config if holder else _selected_config()
    return digest({'config': config.model_dump(mode='json') if config else None,
                   'files': [hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in paths]})


def _selected_config():
    import yaml

    selected = settings()
    try:
        return read_config(selected.config_path, selected.calibration_path) if selected.config_path.is_file() else None
    except (ValueError, OSError, yaml.YAMLError):
        return None  # Invalid bytes still participate in revision; never use a template.


def require_disk_current():
    loaded = state().config_disk_revision
    if loaded is not None and loaded != file_revision(settings().config_path):
        raise APIError('config_changed_on_disk', 'Another process changed configuration. Restart/reload the server before editing.', status_code=409)


def require_revision(expected):
    require_disk_current()
    if expected != revision():
        raise APIError('connection_conflict', 'Configuration, authorization or Telegram selection changed. Refresh and review again.', status_code=409)


@contextmanager
def editing(selected=None):
    try:
        with configuration_lock((selected or settings()).data_dir):
            yield
    except BlockingIOError:
        raise APIError('configuration_busy', 'Another configuration owner is saving. Refresh before trying again.', status_code=409) from None


@contextmanager
def reading():
    """Read-only snapshot: concurrent GETs share the lock; only a real save makes it busy."""
    try:
        with configuration_lock(settings().data_dir, shared=True):
            yield
    except BlockingIOError:
        raise APIError('configuration_busy', 'Another configuration owner is saving. Refresh before trying again.', status_code=409) from None


def publish(config: GoalsConfig):
    """Called under editing(); no corpus/model/inference work for routing-only edits."""
    write_user_config(settings().config_path, config)
    state().app_state = AppState(config=config)
    state().config_disk_revision = file_revision(settings().config_path)
    state().invalidate_stage_clients()


def receipt_path(operation_id):
    return settings().data_dir / 'llm_connections' / (str(UUID(str(operation_id))) + '.json')


def load(operation_id):
    path = receipt_path(operation_id)
    if not path.is_file():
        raise APIError('connection_not_found', 'No confirmed registration receipt. Read configuration before retrying.', status_code=404)
    if path.is_symlink():
        raise APIError('connection_unavailable', 'Connection receipt unavailable', status_code=503)
    return json.loads(path.read_text(encoding='utf-8'))


def save(receipt):
    write_json_atomic(receipt_path(receipt['operation_id']), receipt)


def approve(receipt):
    """Derived task-receipt pointer, not another provider/client store.

    Records the slot's credential fingerprint when a key resolves; without one the approval
    keeps the strict whole-revision binding.
    """
    from zotero_summarizer.services.llm.credential_fingerprint import fingerprint

    pointer = {'operation_id': receipt['operation_id']}
    provider = receipt['provider']
    if provider.get('auth_mode', 'legacy') != 'none' and (current := fingerprint(provider['api_key_env'])):
        pointer['credential_fingerprint'] = current
    write_json_atomic(approval_path(provider['name']), pointer)


def approval_path(name):
    return settings().data_dir / 'llm_connections' / ('approval-' + digest(name) + '.json')


def persisted_config():
    import yaml

    path = settings().config_path
    if not path.is_file():
        return None
    return GoalsConfig.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))


def current_provider(receipt):
    holder = state().app_state
    config = holder.config if holder else _selected_config()
    if config is None:
        return None
    return next((p for p in config.llm_routing.providers if p.name == receipt['provider']['name']), None)
