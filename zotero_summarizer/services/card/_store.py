"""Durable storage + spend guards for the Today AI card (Todo #92).

One JSON file per paper next to the facts cache (under ``Settings.card_dir``),
keyed by the paper's STABLE identity and carrying the generation ``identity``
(card_version + prompt hash + model) so a prompt/model change transparently
misses the cache. Three operational guards keep background generation bounded:

* per-paper call cap — at most ``ZS_CARD_CALL_CAP`` generate attempts per paper;
* daily token cap — a date-stamped ledger refuses work past ``ZS_CARD_DAILY_TOKENS``;
* circuit breaker — ``ZS_CARD_BREAKER_N`` consecutive failures serve facts-only
  for ``ZS_CARD_BREAKER_MINUTES`` minutes.

All knobs are environment settings (the ``ZS_RELATED_*`` pattern) — operational,
never part of the review identity.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import LOGGER, settings


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} must be an integer") from exc
    if not lo <= value <= hi:
        raise ValueError(f"{name}={raw!r} must be in [{lo}, {hi}]")
    return value


def call_cap() -> int:
    return _int_env("ZS_CARD_CALL_CAP", 3, 1, 20)


def daily_token_cap() -> int:
    return _int_env("ZS_CARD_DAILY_TOKENS", 2_000_000, 1000, 1_000_000_000)


def breaker_threshold() -> int:
    return _int_env("ZS_CARD_BREAKER_N", 5, 1, 100)


def breaker_minutes() -> int:
    return _int_env("ZS_CARD_BREAKER_MINUTES", 15, 1, 1440)


def _dir() -> Path:
    return settings().card_dir


def card_path(stable_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stable_key)[:120]
    return _dir() / "cards" / f"{safe}.json"


def load(stable_key: str) -> dict[str, Any] | None:
    try:
        return json.loads(card_path(stable_key).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def load_fresh(stable_key: str, identity: str) -> dict[str, Any] | None:
    """Cached record only when its generation identity matches the current one."""
    record = load(stable_key)
    if record and record.get("identity") == identity:
        return record
    return None


def save(stable_key: str, record: dict[str, Any]) -> None:
    path = card_path(stable_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1))
    except OSError as exc:
        LOGGER.info("card store: write failed for %s: %s", stable_key, exc)


def attempts(stable_key: str, identity: str) -> int:
    record = load(stable_key)
    if record and record.get("identity") == identity:
        return int(record.get("attempts") or 0)
    return 0


def can_attempt(stable_key: str, identity: str) -> bool:
    return attempts(stable_key, identity) < call_cap()


# --- daily token ledger -----------------------------------------------------
def _ledger_path() -> Path:
    return _dir() / "token_ledger.json"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _ledger() -> dict[str, Any]:
    try:
        data = json.loads(_ledger_path().read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    if data.get("date") != _today():
        return {"date": _today(), "tokens": 0}
    return {"date": _today(), "tokens": int(data.get("tokens") or 0)}


def tokens_remaining() -> int:
    return max(0, daily_token_cap() - _ledger()["tokens"])


def add_tokens(count: int) -> None:
    ledger = _ledger()
    ledger["tokens"] += max(0, int(count))
    try:
        _ledger_path().parent.mkdir(parents=True, exist_ok=True)
        _ledger_path().write_text(json.dumps(ledger))
    except OSError as exc:
        LOGGER.info("card store: token ledger write failed: %s", exc)


# --- circuit breaker --------------------------------------------------------
def _breaker_path() -> Path:
    return _dir() / "breaker.json"


def _breaker() -> dict[str, Any]:
    try:
        data = json.loads(_breaker_path().read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    return {"consecutive": int(data.get("consecutive") or 0), "open_until": int(data.get("open_until") or 0)}


def _write_breaker(data: dict[str, Any]) -> None:
    try:
        _breaker_path().parent.mkdir(parents=True, exist_ok=True)
        _breaker_path().write_text(json.dumps(data))
    except OSError as exc:
        LOGGER.info("card store: breaker write failed: %s", exc)


def breaker_open() -> bool:
    return _breaker()["open_until"] > int(time.time())


def record_success() -> None:
    _write_breaker({"consecutive": 0, "open_until": 0})


def record_failure() -> None:
    state = _breaker()
    consecutive = state["consecutive"] + 1
    open_until = state["open_until"]
    if consecutive >= breaker_threshold():
        open_until = int(time.time()) + breaker_minutes() * 60
    _write_breaker({"consecutive": consecutive, "open_until": open_until})


def spend_blocked() -> str | None:
    """Reason a generation must be refused before any model call, or None."""
    if breaker_open():
        return "circuit_open"
    if tokens_remaining() <= 0:
        return "daily_token_cap"
    return None


__all__ = [
    "add_tokens", "breaker_open", "call_cap", "can_attempt", "card_path",
    "daily_token_cap", "load", "load_fresh", "record_failure", "record_success",
    "save", "spend_blocked", "tokens_remaining",
]
