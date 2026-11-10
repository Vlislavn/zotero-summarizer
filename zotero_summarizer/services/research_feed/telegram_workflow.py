"""Sources action/status orchestration; GET/save never activates Telegram or models."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
import uuid

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.research_feed import TelegramSourceSettings
from zotero_summarizer.services._common import read_config, now_iso_z, write_json_atomic
from zotero_summarizer.services._adapters import require_loopback_endpoint
from zotero_summarizer.services.research_feed import profile as profiles, telegram_processing, telegram_progress, telegram_selection
from zotero_summarizer.services.research_feed.telegram_selection import output_dir as _output_dir
from zotero_summarizer.storage import repositories, research_feed_telegram as store

STALE_SECONDS = 900  # the child is killed after wall+reserve (<=~620 s); a 'collecting' state older than this lost its server


def sources(settings):
    profile = profiles.load_profile(settings.data_dir, persist_default=False)
    config = read_config(settings.config_path, settings.calibration_path)
    local_names, providers = [], []
    for provider in config.llm_routing.providers:
        if provider.type != "openai":
            continue
        try:
            require_loopback_endpoint(provider.base_url or "")
            local_names.append(provider.name)
        except ValueError:
            providers.append(provider.name)  # non-loopback: usable only through remote consent
    return {"sources": [target.model_dump() for target in telegram_selection.targets(profile)],
            "model": profile.telegram_model.model_dump() if profile.telegram_model else None,
            "limits": profile.telegram_limits.model_dump(), "local_providers": local_names,
            "llm_enabled": config.llm_enabled, "providers": providers,
            "processing": telegram_processing.describe(settings, config, profile)}


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
    from zotero_summarizer.services.llm.connection_state import editing

    with _exclusive(settings), editing(settings):
        profiles.save_telegram(settings.data_dir, selection)
    return sources(settings)


def _alive(pid) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _stale(value) -> bool:
    """A 'collecting' state whose server died (restart) or never finished within the child's wall."""
    if value.get("state") != "collecting":
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value["updated_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return True
    return age > STALE_SECONDS or not _alive(value.get("pid"))


def status(settings):
    path = _output_dir(settings) / "operation.json"
    consented = telegram_processing.collect_allowed(settings)
    if not path.exists():
        return {"state": "idle", "running": False, "consented": consented, "connection": "unknown",
                "verified_at": None, "message": "Not verified in this workflow. One-time authorization stays in the terminal."}
    if path.is_symlink():
        raise APIError("telegram_status_unavailable", "Telegram status is unavailable", status_code=503)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        # Closed projection: no receipt/session/path/account-owner fields enter HTTP.
        out = {key: value[key] for key in ("state", "connection", "verified_at", "updated_at", "operation_id",
                                           "error", "started_at", "retry_at") if key in value}
        if _stale(value):
            out.update(state="error", error="interrupted")
        out["running"] = out.get("state") == "collecting"
        out["consented"] = consented
        if out["running"]:
            out["progress"] = telegram_progress.read(settings)
            out["elapsed_s"] = max(0, round((datetime.now(timezone.utc) - datetime.fromisoformat(value["started_at"])).total_seconds()))
        return out
    except (OSError, ValueError, TypeError, KeyError):
        raise APIError("telegram_status_unavailable", "Telegram status is unavailable", status_code=503) from None


def _retry_at(settings):
    """Account flood-wait deadline still in the future, else None."""
    try:
        with closing(repositories._connect_to(settings.triage_db_path)) as conn:
            value = store.read_retry_at(conn)
        return value if value and datetime.fromisoformat(value) > datetime.now(timezone.utc) else None
    except Exception:
        return None


def begin(settings, *, run_collect=None):
    """Validate and publish the 'collecting' state; returns ``execute()`` that runs the one pipeline.

    Pipeline: collect_pages -> digest -> persisted today digest. ``run_collect(settings,
    profile, config)`` performs the owned collection; the default live path runs it inside the
    dedicated Telegram child. The offline harness injects a fake collector. ``execute`` never
    raises: it records ``reviewed`` or ``error`` in operation.json. The caller holds ``_exclusive``.
    """
    profile = profiles.load_profile(settings.data_dir, persist_default=False)
    if not telegram_selection.targets(profile):
        raise APIError("telegram_sources_required", "Save at least one Telegram source", status_code=409)
    config = read_config(settings.config_path, settings.calibration_path)
    try:
        telegram_processing.resolve(settings, config, profile)
    except ValueError:
        raise APIError("telegram_local_required",
                       "Select an enabled Telegram provider and model (non-local needs your consent)", status_code=409) from None
    path = _output_dir(settings) / "operation.json"
    if path.is_symlink():
        raise ValueError("Telegram operation state cannot be a symlink")
    state = {"operation_id": uuid.uuid4().hex, "state": "collecting", "connection": "unknown",
             "verified_at": None, "error": None, "pid": os.getpid(), "started_at": now_iso_z()}

    def update(**values):
        state.update(values, updated_at=now_iso_z())
        write_json_atomic(path, state)

    telegram_progress.clear(settings)
    update()

    def execute():
        try:
            collect = run_collect
            if collect is None:
                from zotero_summarizer.services.research_feed import telegram_live
                collect = telegram_live.collect
            collect(settings, profile, config)
            update(state="reviewed", connection="verified", verified_at=now_iso_z(), retry_at=_retry_at(settings))
        except Exception as error:
            code = getattr(error, "code", None)
            if code in {"missing_session", "session_unauthorized", "revoked_session"}:
                update(state="error", connection="needs_auth", verified_at=None, error=code)
            elif code in {"deferred", "busy", "model_unavailable", "telegram_incomplete"}:
                update(state="error", error=code, retry_at=_retry_at(settings))
            else:
                update(state="error", error="telegram_action_failed")
        finally:
            telegram_progress.clear(settings)
    return execute
