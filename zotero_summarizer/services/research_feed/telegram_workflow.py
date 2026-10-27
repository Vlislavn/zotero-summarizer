"""Sources action/status orchestration; GET/save never activates Telegram or models."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import uuid

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.research_feed import TelegramSourceSettings
from zotero_summarizer.models.telegram_discussion import TelegramConversation
from zotero_summarizer.services._common import read_config, now_iso_z, write_json_atomic
from zotero_summarizer.services._adapters import require_loopback_endpoint
from zotero_summarizer.services.research_feed import profile as profiles, telegram_conversations, telegram_review
from zotero_summarizer.services.research_feed.telegram import _output_dir


def sources(settings):
    profile = profiles.load_profile(settings.data_dir, persist_default=False)
    config = read_config(settings.config_path, settings.calibration_path)
    local_names = []
    for provider in config.llm_routing.providers:
        try:
            require_loopback_endpoint(provider.base_url or "")
        except ValueError:
            continue
        if provider.type == "openai":
            local_names.append(provider.name)
    return {"sources": [target.model_dump() for target in telegram_conversations.targets(profile)],
            "model": profile.telegram_model.model_dump() if profile.telegram_model else None,
            "limits": profile.telegram_limits.model_dump(), "local_providers": local_names,
            "llm_enabled": config.llm_enabled}


@contextmanager
def _exclusive(settings):
    import fcntl

    root = _output_dir(settings)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "operation.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise APIError("telegram_busy", "A Telegram action already owns this selection", status_code=409) from None
        yield
    finally:
        os.close(fd)


def save_sources(settings, selection: TelegramSourceSettings):
    with _exclusive(settings):
        profiles.save_telegram(settings.data_dir, selection)
    return sources(settings)


def status(settings):
    path = _output_dir(settings) / "operation.json"
    if not path.exists():
        return {"state": "idle", "connection": "unknown", "verified_at": None,
                "message": "Not verified in this workflow. One-time authorization stays in the terminal."}
    if path.is_symlink():
        raise APIError("telegram_status_unavailable", "Telegram status is unavailable", status_code=503)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        # Closed projection: no receipt/session/path/account-owner fields enter HTTP.
        return {key: value[key] for key in ("state", "connection", "verified_at", "updated_at", "operation_id",
                                           "report_id", "error", "collection_report_id") if key in value}
    except (OSError, ValueError, TypeError, KeyError):
        raise APIError("telegram_status_unavailable", "Telegram status is unavailable", status_code=503) from None


def run(settings, *, consent: bool):
    if consent is not True:
        raise APIError("telegram_consent_required", "Confirm bounded collection and local processing", status_code=409)
    with _exclusive(settings):
        return _run_owned(settings)


def _run_owned(settings):
    profile = profiles.load_profile(settings.data_dir, persist_default=False)
    if not telegram_conversations.targets(profile):
        raise APIError("telegram_sources_required", "Save at least one Telegram source", status_code=409)
    config = read_config(settings.config_path, settings.calibration_path)
    try:
        telegram_review.resolve_local(config, profile)
    except ValueError:
        raise APIError("telegram_local_required", "Select an enabled local Telegram provider and model", status_code=409) from None
    path = _output_dir(settings) / "operation.json"
    if path.is_symlink():
        raise ValueError("Telegram operation state cannot be a symlink")
    state = {"operation_id": uuid.uuid4().hex, "state": "collecting", "connection": "unknown",
             "verified_at": None, "report_id": None, "collection_report_id": None, "error": None}

    def update(**values):
        state.update(values, updated_at=now_iso_z())
        write_json_atomic(path, state)

    update()
    try:
        from zotero_summarizer.cli._telegram import run_child

        receipt = run_child(settings, "conversations")
        collected = receipt["result"]["collection"]
        if collected.get("authorized") is not True:
            raise ValueError("unverified Telegram collection")
        update(state="reviewing", connection="verified", verified_at=now_iso_z(),
               collection_report_id=receipt["export"]["run_id"])
        current = profiles.load_profile(settings.data_dir, persist_default=False)
        if current != profile:
            raise ValueError("Telegram selection changed during collection")
        batches = [TelegramConversation.model_validate(value) for value in collected["conversations"]]
        selected = {target.key for target in telegram_conversations.targets(profile)}
        if {batch.target.key for batch in batches} != selected:
            raise ValueError("Telegram child returned a different selection")
        result = telegram_conversations.publish(settings, batches, collection=receipt["result"],
                                                  profile=profile, config=config)
        update(state=result["status"], report_id=result["run_id"],
               error="review_failed" if result["errors"] else None)
    except Exception as error:
        code = getattr(error, "code", None)
        collection_report_id = getattr(error, "collection_report_id", None)
        if collection_report_id is not None:
            update(collection_report_id=collection_report_id)
        if code in {"missing_session", "session_unauthorized", "revoked_session"}:
            update(state="error", connection="needs_auth", verified_at=None, error=code)
        else:
            update(state="error", error="telegram_action_failed")
    return status(settings)
