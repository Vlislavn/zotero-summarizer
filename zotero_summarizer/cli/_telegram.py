"""One-shot Telegram child (auth or digest); the receipt is verified before the parent proceeds."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class OptionalTelegramMissing(RuntimeError):
    pass


class TelegramChildFailed(RuntimeError):
    def __init__(self, code="telegram_incomplete"):
        self.code = code if code in {"missing_session", "session_unauthorized", "revoked_session", "busy", "deferred"} else "telegram_incomplete"
        super().__init__(self.code)


def require_terminal() -> None:
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise ValueError("Telegram auth requires an interactive terminal")


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


def _verify(settings, purpose: str, receipt: dict) -> dict:
    if (receipt.get("schema_version") != 1 or receipt.get("purpose") != purpose
            or receipt.get("data_dir") != str(settings.data_dir)):
        raise RuntimeError("invalid Telegram child receipt")
    result = receipt["result"]
    if result["status"] != "collected" or result["cleanup"] != "completed":
        raise RuntimeError("Telegram did not complete: " + result["status"])
    if purpose == "auth" and result["collection"] != {"authorized": True}:
        raise RuntimeError("invalid Telegram authorization receipt")
    return receipt  # digest persists telegram-today.json in-child; there is no export bundle


def run_child(settings, purpose: str) -> dict:
    from zotero_summarizer.integrations.telegram_session import _budgets

    if purpose not in {"auth", "digest"}:
        raise ValueError("invalid Telegram process purpose")
    if purpose == "auth":
        require_terminal()
    wall, reserve = _budgets()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".telegram-", dir=settings.data_dir) as temporary:
        path = Path(temporary) / "receipt.json"
        source_root = Path(__file__).resolve().parents[2]
        reentry = ("import sys;sys.path.insert(0,sys.argv[1]);"
                   "from zotero_summarizer.cli import main;raise SystemExit(main(sys.argv[2:]))")
        command = [sys.executable, "-I", "-B", "-c", reentry, str(source_root), "research-feed",
                   "_telegram-child", "--purpose", purpose, "--receipt", str(path),
                   "--project-root", str(settings.project_root)]
        completed = subprocess.run(
            command, cwd=source_root, close_fds=True, timeout=wall + reserve,
            stdin=None if purpose == "auth" else subprocess.DEVNULL,
            stdout=None if purpose == "auth" else subprocess.DEVNULL,
            stderr=None if purpose == "auth" else subprocess.DEVNULL,
        )
        if completed.returncode != 0:
            failure = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
            if failure is not None and failure.get("error") == "optional_dependency_missing":
                raise OptionalTelegramMissing("Telegram requires the optional telegram extra")
            if purpose == "digest":
                raise TelegramChildFailed(failure.get("result", {}).get("status") if failure else None)
            if failure is not None and "result" in failure:
                print(json.dumps({"status": "telegram_incomplete", "result": failure["result"]}, indent=2))
            raise RuntimeError("Telegram child failed; saved pages remain durable; no model work started")
        if not path.is_file():
            raise RuntimeError("Telegram child exited without a receipt")
        return _verify(settings, purpose, json.loads(path.read_text(encoding="utf-8")))


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
