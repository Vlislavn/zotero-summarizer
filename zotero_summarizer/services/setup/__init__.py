"""Setup exports resolve on use; importing path/deployment validation starts no services."""
from importlib import import_module

_EXPORTS = {
    "bootstrap_phase0": "bootstrap", "detect_zotero_data_dirs": "detect",
    "get_setup_status": "status", "validate_config_draft": "validate",
    "write_env_paths": "env_writer",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
    globals()[name] = value
    return value
