"""Public composition with fake child/ML/LLM; no armed owner in pytest."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from test_research_feed_telegram import ExternalGates, _posts, _project, _report, _run, _write_inputs
from zotero_summarizer import cli
from zotero_summarizer.services.research_feed import telegram


def test_live_cli_sqlite_shadow_gates_and_artifact_after_verified_child(tmp_path, monkeypatch, capsys):
    from zotero_summarizer.cli import _telegram
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.model import reranker

    settings = _project(tmp_path)
    posts = _posts()
    gates = ExternalGates(posts)
    calls = []
    collection = {"status": "collected", "cleanup": "completed", "account": {"retry_at": None},
                  "collection": {"counts": dict(fetched=6, new=6, updated=0, unchanged=0, skipped=1),
                                 "channels": [{"username": "alpha", "cursor": {"confirmed_message_id": 9}}],
                                 "account": {"status": "not_deferred", "retry_at": None}}}

    def child(command, **options):
        assert options["stdin"] is subprocess.DEVNULL
        assert "api_hash" not in " ".join(command)
        calls.append("child-exited")
        inputs = tmp_path / "permitted.jsonl"
        _write_inputs(inputs, posts)
        telegram.run_saved(settings, input_jsonl=inputs, collect_only=True)
        exported = telegram.run_saved(settings, collect_only=True, live_collection=collection)
        receipt = _telegram._receipt(settings, "collect", collection, exported)
        Path(command[command.index("--receipt") + 1]).write_text(json.dumps(receipt))
        return subprocess.CompletedProcess(command, 0)

    def model(name):
        assert calls == ["child-exited"]
        return gates.get_reranker(name)

    monkeypatch.setattr(_telegram.subprocess, "run", child)
    monkeypatch.setattr(reranker, "get_reranker", model)
    monkeypatch.setattr(factory, "build_client_for_provider", gates.build_client)

    receipt = _run(tmp_path, capsys)
    payload, _ = _report(receipt)
    assert payload["metadata"]["collection"] == collection
    assert payload["metadata"]["counts"]["fetched"] == 6
    assert payload["metadata"]["counts"]["new"] == 6
    assert payload["metadata"]["live_cursor"] == collection["collection"]["channels"]
    assert payload["metadata"]["hard_gate"] is False
    assert payload["cards"][0]["source_id"] == "telegram:101:7"
    assert payload["metadata"]["counts"]["llm_attempts"] == 4
    assert payload["metadata"]["counts"]["pending"] == 1
    assert payload["metadata"]["counts"]["errors"] == 1


@pytest.fixture
def matching_telegram_export(tmp_path):
    from zotero_summarizer.cli import _telegram

    settings = _project(tmp_path)
    inputs = tmp_path / "input.jsonl"
    posts = _posts()
    _write_inputs(inputs, posts)
    telegram.run_saved(settings, input_jsonl=inputs, collect_only=True)
    account = {"status": "not_deferred", "retry_at": None}
    collection = {"status": "collected", "cleanup": "completed", "account": account,
                  "collection": {"counts": dict(fetched=6, new=6, updated=0, unchanged=0, skipped=1),
                                 "channels": [], "account": account}}
    exported = telegram.run_saved(settings, collect_only=True, live_collection=collection)
    payload, _ = _report(exported)
    assert payload["metadata"]["collection"] == collection
    return settings, _telegram._receipt(settings, "collect", collection, exported)


@pytest.mark.parametrize("returncode,tamper", [(1, False), (0, True)])
def test_failed_child_or_changed_export_never_processes(
    matching_telegram_export, monkeypatch, returncode, tamper,
):
    from zotero_summarizer.cli import _telegram

    settings, receipt = matching_telegram_export
    before = {name: Path(artifact["path"]).read_bytes() for name, artifact in receipt["files"].items()}
    child_returncode = 0

    def child(command, **options):
        Path(command[command.index("--receipt") + 1]).write_text(json.dumps(receipt))
        return subprocess.CompletedProcess(command, child_returncode)

    monkeypatch.setattr(_telegram.subprocess, "run", child)
    assert _telegram.run_child(settings, "collect") == receipt

    child_returncode = returncode
    if tamper:
        Path(receipt["export"]["inputs_path"]).write_text("changed")
    assert {name for name, artifact in receipt["files"].items()
            if Path(artifact["path"]).read_bytes() != before[name]} == ({"inputs_path"} if tamper else set())
    with pytest.raises(RuntimeError) as caught:
        _telegram.run_child(settings, "collect")
    assert str(caught.value) == (
        "Telegram child export verification failed" if tamper else
        "Telegram child failed; saved pages remain durable; no model work started"
    )


def test_changed_export_has_no_non_hash_rejection(matching_telegram_export, monkeypatch):
    from zotero_summarizer.cli import _telegram

    settings, receipt = matching_telegram_export
    assert _telegram._verify(settings, "collect", receipt) is receipt
    changed = Path(receipt["export"]["inputs_path"])
    changed.write_text("changed")
    recorded_hash = receipt["files"]["inputs_path"]["sha256"]
    actual_digest = _telegram._digest
    observed = []

    def replay_original_digest(path):
        actual = actual_digest(path)
        observed.append((path, actual))
        return recorded_hash if path == changed else actual

    monkeypatch.setattr(_telegram, "_digest", replay_original_digest)
    assert _telegram._verify(settings, "collect", receipt) is receipt
    assert [path for path, _ in observed] == [Path(artifact["path"]) for artifact in receipt["files"].values()]
    assert dict(observed)[changed] != recorded_hash


def test_replay_and_cached_never_launch_telegram(tmp_path, monkeypatch, capsys):
    from zotero_summarizer.cli import _telegram
    settings = _project(tmp_path)
    inputs = tmp_path / "input.jsonl"
    _write_inputs(inputs, _posts())
    def forbidden(*args, **kwargs):
        raise AssertionError("Telegram must not run for saved inputs")
    monkeypatch.setattr(_telegram, "run_child", forbidden)
    _run(tmp_path, capsys, "--input-jsonl", inputs, "--collect-only")
    _run(tmp_path, capsys, "--cached-only")
    assert not (settings.data_dir / "telegram-account").exists()


def test_auth_requires_tty_before_settings_or_child(monkeypatch):
    from zotero_summarizer.cli import _telegram
    from zotero_summarizer.settings import Settings
    monkeypatch.setattr(_telegram.sys.stdin, "isatty", lambda: False)
    def forbidden(**kwargs):
        raise AssertionError("Settings must not load")
    monkeypatch.setattr(Settings, "load", forbidden)
    with pytest.raises(SystemExit) as caught:
        cli.main(["research-feed", "telegram-auth"])
    assert caught.value.code == 2


def test_missing_optional_sdk_is_explicit(monkeypatch):
    import builtins
    from zotero_summarizer.cli import _telegram
    real_import = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "telethon.sessions":
            raise ModuleNotFoundError("not installed", name="telethon")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(RuntimeError) as caught:
        _telegram._sdk_session_type()
    assert str(caught.value) == "Telegram requires the optional telegram extra"


def test_rss_cached_dispatch_never_imports_telegram_or_opens_its_state(tmp_path, monkeypatch, capsys):
    import builtins
    from zotero_summarizer.services import research_feed
    real_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith("telethon") or name in {
            "zotero_summarizer.integrations.telegram", "zotero_summarizer.integrations.telegram_session",
            "zotero_summarizer.cli._telegram",
        }:
            raise AssertionError("RSS imported Telegram")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(research_feed, "run_weekly", lambda settings, **kwargs: {"source": "app_rss"})
    assert cli.main(["research-feed", "run", "--from", "2026-10-01", "--to", "2026-10-08",
                     "--cached-only", "--project-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"source": "app_rss"}
    assert not (tmp_path / "data").exists()


def test_changed_live_snapshot_is_not_reimported_over_newer_originals(tmp_path, monkeypatch):
    settings = _project(tmp_path)
    posts = _posts()
    inputs = tmp_path / "input.jsonl"
    _write_inputs(inputs, posts)
    exported = telegram.run_saved(settings, input_jsonl=inputs, collect_only=True)
    posts[0]["text"] = "synthetic later edit"
    _write_inputs(inputs, posts)
    telegram.run_saved(settings, input_jsonl=inputs, collect_only=True)
    collection = {"status": "collected", "cleanup": "completed", "collection": {"counts": {}}, "account": {}}
    def forbidden(*args):
        raise AssertionError("changed snapshot reached model processing")
    monkeypatch.setattr(telegram, "_process", forbidden)
    with pytest.raises(RuntimeError):
        telegram.run_saved(settings, input_jsonl=Path(exported["inputs_path"]), live_collection=collection)
    from contextlib import closing
    from zotero_summarizer.storage import repositories, research_feed_telegram as store
    with closing(repositories._connect_to(settings.triage_db_path)) as conn:
        assert store.list_posts(conn, limit=1000)[0]["post"].text == "synthetic later edit"
