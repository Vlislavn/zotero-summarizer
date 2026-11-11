"""The ONE seam that turns the Settings 'Checklist engine' (Off / Local / Cloud) into a Jev ``Target`` (REQ-S35, #147).

Deep reviews (``resolve``) and Telegram's disagreement check (``noul_fn``) both get their engine here; the Cloud key lives in the OS keyring
(``TYPESAFE_API``, the existing credential store) and is never returned or logged. ``ZS_OFFLINE`` refuses Cloud."""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from zotero_summarizer.integrations import jev
from zotero_summarizer.services.llm.credentials import get_api_key

KEY_NAME = "TYPESAFE_API"
_OFFLINE = {"1", "true", "yes"}


def key_present() -> bool:
    return get_api_key(KEY_NAME) is not None


def target(engine: str, local_url: str = jev.LOCAL_URL) -> jev.Target | None:
    """Where to send requests; None when the engine is off, the cloud key is missing, or the run is offline (cloud only)."""
    if engine == "local":
        return jev.Target("local", (local_url or jev.LOCAL_URL).strip())
    if engine == "cloud" and (os.environ.get("ZS_OFFLINE") or "").strip().lower() not in _OFFLINE:
        found = get_api_key(KEY_NAME)
        return jev.Target("cloud", jev.CLOUD_URL, found[0]) if found else None
    return None


def resolve(config: Any) -> jev.Target | None:
    """The configured engine's target (deep reviews call ``jev.ask`` with it); None when Off/unusable."""
    return target(config.checklist_engine, config.checklist_local_url)


def noul_fn(config: Any) -> Callable[[dict, dict], dict[str, float] | None] | None:
    """``noul(state, questions) -> {id: P(yes)} | None`` for Telegram's disagreement check; None → no block."""
    t = resolve(config)
    return None if t is None else (lambda state, questions: jev.noul(t, state, questions))


def noul_for(settings: Any) -> Callable[[dict, dict], dict[str, float] | None] | None:
    """``noul_fn`` of the saved configuration; None (no disagreement block) when there is no readable goals file."""
    from zotero_summarizer.services._common import read_config

    try:
        return noul_fn(read_config(settings.config_path, settings.calibration_path))
    except (OSError, ValueError):
        return None


def check(engine: str, local_url: str = jev.LOCAL_URL) -> dict[str, Any]:
    """Settings 'Test': one tiny Noul request to the chosen engine. Never returns the key or the answer text."""
    if engine == "off":
        return {"ok": True, "message": "Off: reviews use the language-model checklist only."}
    t = target(engine, local_url)
    if t is None:
        return {"ok": False, "message": "No key saved for the cloud engine, or the app runs offline."}
    answer = jev.noul(t, {"text": "The code is available at https://github.com/example/repo."},
                      {"t": {"type": "noul", "instructions": "Does `text` give a link to code?"}}, timeout=20.0)
    if answer is None:
        message = ("The cloud engine rejected the access key or could not be reached." if engine == "cloud"
                   else "No valid answer. Check the address and that the engine is running.")
        return {"ok": False, "message": message}
    return {"ok": True, "message": "The engine answered."}
