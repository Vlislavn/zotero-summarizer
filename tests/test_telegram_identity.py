"""Synthetic name-only storage; no system keyring or M3 entrypoint is called."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from zotero_summarizer.cli import _telegram_identity as identity
from zotero_summarizer.services.llm import credentials


SECRET = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def account(tmp_path, monkeypatch):
    directory = tmp_path / "telegram-account"
    directory.mkdir(mode=0o700)
    values = {}
    monkeypatch.setattr(credentials, "keyring", SimpleNamespace(
        get_password=lambda service, name: values.get((service, name)),
        set_password=lambda service, name, secret: values.__setitem__((service, name), secret),
        errors=SimpleNamespace(KeyringError=type("KeyringError", (Exception,), {})),
    ))
    return tmp_path, values


def test_identity_provisions_own_id_and_name_only_then_noninteractive_reuse(account, monkeypatch):
    root, values = account
    entries = iter(["12345", "TELEGRAM_FIXTURE"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(entries))
    monkeypatch.setattr(identity, "getpass", lambda prompt: SECRET)
    assert identity.resolve(root, authenticate=True) == (12345, SECRET)
    path = root / "telegram-account" / "application.json"
    assert json.loads(path.read_text()) == {"schema_version": 1, "api_id": 12345,
                                           "api_hash_name": "TELEGRAM_FIXTURE"}
    assert SECRET not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert values == {(credentials._SERVICE, "TELEGRAM_FIXTURE"): SECRET}
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("normal run prompted"))
    monkeypatch.setattr(identity, "getpass", lambda prompt: pytest.fail("normal run prompted"))
    assert identity.resolve(root, authenticate=False) == (12345, SECRET)


def test_normal_missing_identity_never_prompts_or_reads_environment(account, monkeypatch):
    root, values = account
    monkeypatch.setenv("TELEGRAM_FIXTURE", SECRET)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("normal run prompted"))
    with pytest.raises(RuntimeError):
        identity.resolve(root, authenticate=False)
    assert values == {} and not (root / "telegram-account" / "application.json").exists()


def test_keyring_failure_stays_loud_without_environment_fallback(account, monkeypatch):
    root, values = account
    path = root / "telegram-account" / "application.json"
    path.write_text(json.dumps({"schema_version": 1, "api_id": 12345,
                               "api_hash_name": "TELEGRAM_FIXTURE"}))
    path.chmod(0o600)
    monkeypatch.setenv("TELEGRAM_FIXTURE", SECRET)
    def unavailable(*args):
        raise RuntimeError("synthetic keyring failure")
    monkeypatch.setattr(credentials.keyring, "get_password", unavailable)
    with pytest.raises(RuntimeError):
        identity.resolve(root, authenticate=False)
    assert values == {}
