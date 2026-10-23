"""One-shot Telegram child; no model work until clean exit and verified export."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class OptionalTelegramMissing(RuntimeError):
    pass


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


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            value.update(block)
    return value.hexdigest()


def _receipt(settings, purpose: str, result: dict, exported: dict | None) -> dict:
    files = {} if exported is None else {
        name: {"path": exported[name], "sha256": _digest(Path(exported[name]))}
        for name in ("json_path", "markdown_path", "inputs_path")}
    return {"schema_version": 1, "purpose": purpose, "data_dir": str(settings.data_dir),
            "result": result, "export": exported, "files": files}


def _verify(settings, purpose: str, receipt: dict) -> dict:
    if (receipt.get("schema_version") != 1 or receipt.get("purpose") != purpose
            or receipt.get("data_dir") != str(settings.data_dir)):
        raise RuntimeError("invalid Telegram child receipt")
    result = receipt["result"]
    if result["status"] != "collected" or result["cleanup"] != "completed":
        raise RuntimeError("Telegram did not complete: " + result["status"])
    if purpose == "auth":
        if result["collection"] != {"authorized": True} or receipt["files"] or receipt["export"] is not None:
            raise RuntimeError("invalid Telegram authorization receipt")
        return receipt
    files = receipt["files"]
    if set(files) != {"json_path", "markdown_path", "inputs_path"}:
        raise RuntimeError("incomplete Telegram child export")
    output = (settings.data_dir / "research_feed" / "telegram").resolve()
    parents = set()
    for name, artifact in files.items():
        path = Path(artifact["path"])
        if (not path.resolve().is_relative_to(output) or path.is_symlink()
                or str(path) != receipt["export"][name] or _digest(path) != artifact["sha256"]):
            raise RuntimeError("Telegram child export verification failed")
        parents.add(path.parent)
    if len(parents) != 1:
        raise RuntimeError("Telegram child export is not one bundle")
    payload = json.loads(Path(receipt["export"]["json_path"]).read_text(encoding="utf-8"))
    if payload["metadata"]["collection"] != result:
        raise RuntimeError("Telegram child export receipt mismatch")
    return receipt


def run_child(settings, purpose: str) -> dict:
    from zotero_summarizer.integrations.telegram_session import _budgets

    if purpose not in {"auth", "collect"}:
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
            if failure is not None and "result" in failure:
                print(json.dumps({"status": "telegram_incomplete", "result": failure["result"],
                                  "export": failure["export"]}, indent=2))
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
        from zotero_summarizer.services.research_feed import telegram, telegram_runtime

        def client_factory(session):
            api_id, api_hash = resolve(settings.data_dir, authenticate=args.purpose == "auth")
            return client(session, api_id, api_hash)

        result = telegram_runtime.collect_once_cli(
            settings, session_factory=lambda path: session_type(str(path)),
            client_factory=client_factory, purpose=args.purpose,
        )
        if result["status"] != "collected" or result["cleanup"] != "completed":
            _write_receipt(path, _receipt(settings, args.purpose, result, None))
            return 1
        exported = (telegram.run_saved(settings, collect_only=True, live_collection=result)
                    if args.purpose == "collect" else None)
        receipt = _receipt(settings, args.purpose, result, exported)
        _verify(settings, args.purpose, receipt)
        _write_receipt(path, receipt)
        if args.purpose == "collect" and any(
            channel["status"] not in {"committed", "same_channel_already_seen"}
            for channel in result["collection"]["channels"]
        ):
            return 1  # Preserve partial pages/export; never silently process a rebound source.
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
