"""Name-only app identity, used only inside the leased/fused Telegram child."""
from __future__ import annotations

import json
import os
from pathlib import Path

from zotero_summarizer.integrations import telegram_session as owner
from zotero_summarizer.integrations.telegram_auth import prompt_secret as getpass


IDENTITY_MAX_BYTES = 4096


def _checked(value: dict) -> dict:
    from zotero_summarizer.services.llm import credentials

    if (type(value) is not dict or set(value) != {"schema_version", "api_id", "api_hash_name"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["api_id"]) is not int or not 1 <= value["api_id"] < 2**31
            or type(value["api_hash_name"]) is not str):
        raise ValueError("invalid Telegram app identity")
    name = credentials._validate_name(value["api_hash_name"])
    if name != value["api_hash_name"]:
        raise ValueError("invalid Telegram credential name")
    return value


def _read(path: Path) -> dict:
    owner._no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        owner._private_file(fd, path)
        raw = os.read(fd, IDENTITY_MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > IDENTITY_MAX_BYTES:
        raise ValueError("invalid Telegram app identity size")
    return _checked(json.loads(raw))


def _hash(value) -> str:
    if type(value) is not str or len(value) != 32 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("invalid or missing Telegram api_hash in OS keyring")
    return value


def _provision(path: Path) -> dict:
    from zotero_summarizer.services.llm import credentials

    try:
        api_id = int(input("Your Telegram application api_id: "))
    except ValueError:
        raise ValueError("invalid Telegram api_id") from None
    value = _checked({"schema_version": 1, "api_id": api_id,
                      "api_hash_name": input("OS keyring credential name (uppercase): ")})
    name = value["api_hash_name"]
    if credentials.keyring.get_password(credentials._SERVICE, name) is not None:
        raise ValueError("credential name already exists; choose a new name")
    secret = _hash(getpass("Your Telegram application api_hash (hidden): "))
    credentials.store_api_key(name, secret)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        owner._private_file(stream.fileno(), path)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return value


def resolve(data_dir: Path, *, authenticate: bool) -> tuple[int, str]:
    from zotero_summarizer.services.llm import credentials

    path = data_dir / "telegram-account" / "application.json"
    try:
        value = _read(path)
    except FileNotFoundError:
        if not authenticate:
            raise RuntimeError("Telegram app identity missing; run research-feed telegram-auth") from None
        value = _provision(path)
    secret = credentials.keyring.get_password(credentials._SERVICE, value["api_hash_name"])
    return value["api_id"], _hash(secret)
