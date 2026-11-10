"""Spawn the one-shot Telegram child (auth or digest); its receipt is verified before the parent proceeds."""
from __future__ import annotations

import json
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
