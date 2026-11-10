"""One-shot Telegram child (auth or digest); the receipt is verified before the parent proceeds."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from zotero_summarizer.integrations.telegram_child import (
    OptionalTelegramMissing, TelegramChildFailed, _verify, require_terminal, run_child,
)


def _sdk_session_type():
    try:
        from telethon.sessions import SQLiteSession
    except ModuleNotFoundError as error:
        if error.name != "telethon":
            raise
        raise OptionalTelegramMissing("Telegram requires the optional telegram extra") from None
    return SQLiteSession


def _receipt(settings, purpose: str, result: dict) -> dict:
    return {"schema_version": 1, "purpose": purpose, "data_dir": str(settings.data_dir), "result": result}


def _write_receipt(path: Path, value: dict) -> None:
    from zotero_summarizer.services._common import write_json_atomic

    try:
        write_json_atomic(path, value)
    except Exception:
        raise RuntimeError("Telegram receipt publication failed") from None


def child(args) -> int:
    from zotero_summarizer.services._common import settings as current_settings

    # Raw SDK exceptions/logs can contain phone/session/credential material.
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    settings = current_settings()
    path = Path(args.receipt)
    from zotero_summarizer.integrations import telegram_session as owner

    owner._no_symlinks(path)
    if (not path.is_relative_to(settings.data_dir) or not path.parent.name.startswith(".telegram-")
            or path.name != "receipt.json" or path.exists()):
        raise ValueError("invalid Telegram child receipt destination")
    try:
        if args.purpose == "auth":
            require_terminal()
        session_type = _sdk_session_type()
        from zotero_summarizer.cli._telegram_identity import resolve
        from zotero_summarizer.integrations.telegram_auth import client
        from zotero_summarizer.services.research_feed import telegram_runtime

        def client_factory(session):
            api_id, api_hash = resolve(settings.data_dir, authenticate=args.purpose == "auth")
            return client(session, api_id, api_hash)

        result = telegram_runtime.collect_once_cli(
            settings, session_factory=lambda path: session_type(str(path)),
            client_factory=client_factory, purpose=args.purpose,
        )
        receipt = _receipt(settings, args.purpose, result)
        if result["status"] != "collected" or result["cleanup"] != "completed":
            _write_receipt(path, receipt)
            return 1
        _verify(settings, args.purpose, receipt)
        _write_receipt(path, receipt)
        return 0
    except Exception as error:
        _write_receipt(path, {"schema_version": 1, "purpose": args.purpose,
                                "error": "optional_dependency_missing" if isinstance(error, OptionalTelegramMissing)
                                else "telegram_command_failed"})
        return 1


def authenticate(args) -> int:
    from zotero_summarizer.services._common import settings as current_settings

    receipt = run_child(current_settings(), "auth")
    print(json.dumps({"status": "authorized", "cleanup": receipt["result"]["cleanup"]}))
    return 0
