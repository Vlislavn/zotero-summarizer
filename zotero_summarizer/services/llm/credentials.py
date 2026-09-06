"""API-key storage backed by the OS keyring, with legacy env fallback."""

from __future__ import annotations

import os
import re

import keyring

from zotero_summarizer.api.errors import APIError

_SERVICE = "zotero-summarizer"
_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _validate_name(name: str) -> str:
    name = name.strip()
    if not _NAME.fullmatch(name):
        raise APIError(
            error="invalid_credential_name",
            message="Credential name must be an uppercase environment-style name",
            status_code=422,
        )
    return name


def get_api_key(name: str) -> tuple[str, str] | None:
    """Return ``(secret, source)``; keyring wins, then the legacy environment."""
    name = _validate_name(name)
    try:
        secret = (keyring.get_password(_SERVICE, name) or "").strip()
    except (keyring.errors.KeyringError, RuntimeError):
        secret = ""
    if secret:
        return secret, "keyring"
    secret = os.getenv(name, "").strip()
    return (secret, "environment") if secret else None


def _credential_status(name: str) -> dict[str, object]:
    resolved = get_api_key(name)
    return {
        "name": _validate_name(name),
        "present": resolved is not None,
        "source": resolved[1] if resolved else None,
    }


def store_api_key(name: str, secret: str) -> dict[str, object]:
    """Write a secret to the OS keyring and return redacted status only."""
    name, secret = _validate_name(name), secret.strip()
    if not secret:
        raise APIError(
            error="empty_api_key", message="API key cannot be empty", status_code=422
        )
    try:
        keyring.set_password(_SERVICE, name, secret)
    except (keyring.errors.KeyringError, RuntimeError) as exc:
        raise APIError(
            error="credential_store_unavailable",
            message="The operating-system credential store is unavailable",
            status_code=503,
        ) from exc
    return {"name": name, "present": True, "source": "keyring"}
