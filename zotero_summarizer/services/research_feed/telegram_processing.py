"""Where Telegram summaries and trend labels are computed (Todo #112, brief §2.5).

Default: the saved Telegram provider must be loopback (``telegram_review.resolve_local``).
A non-loopback provider is admitted ONLY while the owner's stored consent token matches
the live boundary — the same pattern as ``services/card/_consent.py``: the token hashes
the resolved provider name + endpoint + model, so changing any of them silently drops the
consent and processing falls back to 'no summary'. Consent is explicit (POST), never
implied by saving sources or by the run consent, and covers sending third-party channel
text (posts and comments) to that provider.
"""
from __future__ import annotations

import hashlib
import json

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import read_config, to_text, write_json_atomic
from zotero_summarizer.services.research_feed import profile as profiles, telegram_review
from zotero_summarizer.services.research_feed.telegram_selection import output_dir as _output_dir
from zotero_summarizer.settings import offline_requested

CONSENT_FILE = "remote-consent.json"
COLLECT_FILE = "collect-consent.json"


def _token(stage) -> str:
    identity = f"{stage.provider.name}|{stage.provider.base_url}|{stage.model}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _stored(settings) -> str | None:
    path = _output_dir(settings) / CONSENT_FILE
    try:
        return None if path.is_symlink() else json.loads(path.read_text(encoding="utf-8")).get("token")
    except (OSError, ValueError, AttributeError):
        return None


def _admit(settings, config, profile):
    """``(stage, local)``: the loopback stage, or the remote one the owner consented to."""
    try:
        return telegram_review.resolve_local(config, profile), True
    except ValueError:
        stage = telegram_review.resolve_local(config, profile, allow_remote=True)
        if offline_requested():
            raise ValueError("offline mode forbids a remote Telegram provider") from None
        if _stored(settings) == _token(stage):
            return stage, False
        raise


def resolve(settings, config, profile):
    """Local stage, or the remote stage the owner consented to; ValueError otherwise."""
    return _admit(settings, config, profile)[0]


def model(settings, config, profile):
    """``prompt -> text`` for summaries/labels on the admitted stage, or None."""
    from zotero_summarizer.services.llm import factory

    try:
        stage, local = _admit(settings, config, profile)
    except ValueError:
        return None

    def call(prompt: str) -> str:
        client = factory.build_client_for_provider(stage.provider, stage.model, local_only=local)
        try:
            return to_text(client.prompt(prompt))
        finally:
            client.close()
    return call


def describe(settings, config, profile) -> dict | None:
    """What the UI shows: provider/model, locality, token and whether remote use is consented."""
    try:
        stage = telegram_review.resolve_local(config, profile, allow_remote=True)
    except ValueError:
        return None
    try:
        telegram_review.resolve_local(config, profile)
        local = True
    except ValueError:
        local = False
    return {"provider": stage.provider.name, "model": stage.model, "local": local, "token": _token(stage),
            "remote_consented": (not local) and _stored(settings) == _token(stage)}


def collect_allowed(settings) -> bool:
    """The owner's remembered OK to read the saved public sources with the Telegram account."""
    path = _output_dir(settings) / COLLECT_FILE
    return path.is_file() and not path.is_symlink()


def set_collect_consent(settings, allow: bool) -> dict:
    """Remember (or revoke) the one-time collection consent behind the «Проверить каналы» button."""
    path = _output_dir(settings) / COLLECT_FILE
    if path.is_symlink():
        raise ValueError("telegram consent file cannot be a symlink")
    if allow:
        write_json_atomic(path, {"allowed": True})
    else:
        path.unlink(missing_ok=True)
    return {"consented": collect_allowed(settings)}


def set_remote_consent(settings, token: str, allow: bool) -> dict:
    """Record (or withdraw) consent for the CURRENT saved remote boundary."""
    profile = profiles.load_profile(settings.data_dir, persist_default=False)
    config = read_config(settings.config_path, settings.calibration_path)
    current = describe(settings, config, profile)
    if current is None or current["local"]:
        raise APIError("telegram_remote_unavailable", "Select a saved non-local Telegram provider first", status_code=409)
    if allow and token != current["token"]:
        raise APIError("processing_boundary_changed", "Telegram provider changed; reload before consenting", status_code=409)
    root = _output_dir(settings)
    root.mkdir(parents=True, exist_ok=True)
    path = root / CONSENT_FILE
    if path.is_symlink():
        raise ValueError("telegram consent file cannot be a symlink")
    if allow:
        write_json_atomic(path, {"token": token})
    else:
        path.unlink(missing_ok=True)
    return describe(settings, config, profile)
