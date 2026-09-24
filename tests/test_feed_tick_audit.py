"""Feed command/tick contracts from A030–A035, without models or live Zotero."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from zotero_summarizer.cli import build_parser
from zotero_summarizer.cli import _feeds as cli
from zotero_summarizer.integrations.app_rss import AppRssReader
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services.triage.feeds import _common, _loop, _tick, _tick_phases
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import feeds, rss


def _report(**changes):
    return _common.DaemonTickReport(**(dict(tick_id="test", fetched=0, skipped_already_processed=0,
        skipped_library_dedup=0, triaged=0, fast_rejected=0, errors=0, marked_read=0,
        outcomes_resolved=0) | changes))


@pytest.mark.parametrize("source", ["app", "zotero"])
def test_unlimited_feed_picker_has_no_hidden_1000_or_5000_cap(tmp_path, source):
    if source == "app":
        db = tmp_path / "triage.db"
        with feeds.open_triage_conn(db) as conn:
            fid = rss.upsert_rss_feed(conn, name="Feed", url="https://example.com/rss")
            conn.executemany("INSERT INTO rss_items(rss_feed_id, stable_feed_key, title, guid) VALUES (?, ?, 'Title', ?)",
                             [(fid, f"feed:k{i}", f"g{i}") for i in range(5001)])
            conn.commit()
        reader = AppRssReader(db)
    else:
        from tests._zotero_fixtures import build_zotero_db, add_feed
        db = build_zotero_db(tmp_path / "zotero")
        add_feed(db, library_id=2, name="Feed")
        fid = 2
        with sqlite3.connect(db) as conn:
            item_type = conn.execute("SELECT itemTypeID FROM itemTypes WHERE typeName='journalArticle'").fetchone()[0]
            conn.executemany("INSERT INTO items(itemID, itemTypeID, libraryID, key) VALUES (?, ?, 2, ?)",
                             [(i + 100, item_type, f"K{i:07}") for i in range(5001)])
            conn.executemany("INSERT INTO feedItems(itemID, guid) VALUES (?, ?)", [(i + 100, f"g{i}") for i in range(5001)])
        reader = ZoteroReader(db.parent)
    all_rows = _tick_phases._pick_unread_batch_round_robin(reader, batch_size=None, feed_library_ids=[fid])
    assert len(all_rows) == len({row["item_id"] for row in all_rows}) == 5001
    assert len(_tick_phases._pick_unread_batch_round_robin(reader, batch_size=7, feed_library_ids=[fid])) == 7


@pytest.mark.parametrize("count", [0, -1])
def test_loop_tick_limit_is_checked_before_any_work(monkeypatch, count):
    calls = []
    monkeypatch.setattr(_loop, "_load_config", lambda: {"feeds": {}})
    monkeypatch.setattr(_loop, "run_daemon_tick", lambda **_: calls.append(True) or _report())
    if count < 0:
        with pytest.raises(ValueError):
            asyncio.run(_loop.run_daemon_loop(max_ticks=count))
    else:
        asyncio.run(_loop.run_daemon_loop(max_ticks=count))
    assert calls == []


def test_report_serializes_fatal_provider_failure():
    assert _report(fatal_llm_error=True).as_dict()["fatal_llm_error"] is True


@pytest.mark.parametrize("command", ["run", "tick"])
@pytest.mark.parametrize("errors,fatal", [(1, False), (0, True)])
def test_feed_commands_return_nonzero_for_reported_failure(tmp_path, monkeypatch, command, errors, fatal):
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services.triage import feeds as facade

    settings = Settings.load(project_root=tmp_path)
    monkeypatch.setattr(cli.Settings, "load", lambda **_: settings)
    monkeypatch.setattr(cli, "_bootstrap_feeds_cli", lambda _: (settings, None))
    monkeypatch.setattr(lifecycle, "startup", lambda **_: None)
    monkeypatch.setattr(_common, "_load_config", lambda: {"feeds": {"daemon_batch_size": 5}})
    monkeypatch.setattr(facade, "run_daemon_tick", lambda **_: _report(errors=errors, fatal_llm_error=fatal))
    args = build_parser().parse_args(["feeds", command])
    assert args.func(args) != 0


@pytest.mark.parametrize("batch", [None, 3])
def test_cli_tick_uses_config_only_when_batch_is_omitted(tmp_path, monkeypatch, batch):
    from zotero_summarizer.services.triage import feeds as facade

    monkeypatch.setattr(cli, "_bootstrap_feeds_cli", lambda _: (Settings.load(project_root=tmp_path), None))
    monkeypatch.setattr(_common, "_load_config", lambda: {"feeds": {"daemon_batch_size": 17}})
    seen = []
    monkeypatch.setattr(facade, "run_daemon_tick", lambda **kw: seen.append(kw["batch_size"]) or _report())
    args = build_parser().parse_args(["feeds", "tick"] + ([] if batch is None else ["--batch-size", str(batch)]))
    assert args.func(args) == 0
    assert seen == [17 if batch is None else batch]


def test_dry_tick_preserves_existing_errors_and_does_not_persist_new_decisions(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    monkeypatch.setattr(_common, "get_settings", lambda: settings)
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        fid = rss.upsert_rss_feed(conn, name="Feed", url="https://example.com/rss")
        for guid in ("old", "new"):
            rss.upsert_rss_item(conn, rss_feed_id=fid, item={"guid": guid, "title": guid})
        conn.commit()
    reader = AppRssReader(settings.triage_db_path)
    items = reader.get_feed_items()
    old = next(item for item in items if item["guid"] == "old")
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        feeds.record_decision(conn, run_id="old", feed_item=old, decision=feeds.DECISION_SKIPPED_ERROR, error="old error")
        conn.commit()
        before = [tuple(row) for row in conn.execute("SELECT * FROM processed_feed_items")]
    refreshed = []
    monkeypatch.setattr(reader, "refresh_feeds", lambda **_: refreshed.append(True))
    monkeypatch.setattr(_tick, "resolve_tick_adapters", lambda *a, **k: (reader, None, None))
    monkeypatch.setattr(_tick, "_load_config", lambda: {"feeds": {"dedup_against_library": False}})
    monkeypatch.setattr(_tick, "_maybe_schedule_gate_retrain", lambda _: None)
    monkeypatch.setattr(_tick, "_apply_classifier_gate", lambda _, rows, **kw: (rows, []))
    monkeypatch.setattr(_tick, "run_triage_stage", lambda rows, **kw: ([], [], [(row, "new error") for row in rows], False))
    report = _tick.run_daemon_tick(reader=reader, dry_run=True, gate_only=True)
    assert report.errors == 2
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        after = [tuple(row) for row in conn.execute("SELECT * FROM processed_feed_items")]
        assert conn.execute("SELECT COUNT(*) FROM rss_items WHERE read_at IS NOT NULL").fetchone()[0] == 0
    assert after == before and refreshed == []


def test_tick_excludes_threads_and_processes_until_it_finishes(tmp_path, monkeypatch):
    import subprocess
    import sys
    import threading
    from concurrent.futures import ThreadPoolExecutor

    settings = Settings.load(project_root=tmp_path)
    monkeypatch.setattr(_common, "get_settings", lambda: settings)
    monkeypatch.setattr(_tick, "_load_config", lambda: {"feeds": {"dedup_against_library": False}})
    monkeypatch.setattr(_tick, "resolve_tick_adapters", lambda *a, **k: (None, None, None))
    monkeypatch.setattr(_tick, "_apply_classifier_gate", lambda _, rows, **kw: (rows, []))
    entered, release = threading.Event(), threading.Event()
    picked = []

    def pick(*args, **kwargs):
        picked.append(True)
        entered.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(_tick, "pick_and_log", pick)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(_tick.run_daemon_tick, dry_run=True, gate_only=True)
        try:
            assert entered.wait(5)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                _tick.run_daemon_tick(dry_run=True, gate_only=True)
            lock_path = settings.triage_db_path.with_suffix(".tick-lock.sqlite")
            code = "import sqlite3,sys\nc=sqlite3.connect(sys.argv[1],timeout=0)\ntry: c.execute('BEGIN EXCLUSIVE')\nexcept sqlite3.OperationalError: print('locked')\nelse: sys.exit(9)"
            other = subprocess.run([sys.executable, "-c", code, str(lock_path)], capture_output=True, text=True, timeout=5)
            assert other.returncode == 0 and other.stdout.strip() == "locked"
            assert picked == [True]
        finally:
            release.set()
        assert first.result(timeout=5).fetched == 0
    assert _tick.run_daemon_tick(dry_run=True, gate_only=True).fetched == 0
    assert picked == [True, True]


def test_tick_lock_releases_after_errors_and_process_exit(tmp_path, monkeypatch):
    import subprocess
    import sys

    settings = Settings.load(project_root=tmp_path)
    monkeypatch.setattr(_common, "get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="work failed"):
        with _common._tick_lock():
            raise RuntimeError("work failed")
    path = settings.triage_db_path.with_suffix(".tick-lock.sqlite")
    subprocess.run([sys.executable, "-c", "import sqlite3,sys,os;c=sqlite3.connect(sys.argv[1],timeout=0);c.execute('BEGIN EXCLUSIVE');os._exit(0)", str(path)], check=True, timeout=5)
    with _common._tick_lock():
        pass


@pytest.mark.parametrize("max_ticks", [0, -1])
def test_cli_serve_checks_tick_limit_before_bootstrap(monkeypatch, max_ticks):
    def boot(_):
        pytest.fail("bootstrap must not run")

    monkeypatch.setattr(cli, "_bootstrap_feeds_cli", boot)
    args = build_parser().parse_args(["feeds", "serve", "--max-ticks", str(max_ticks)])
    if max_ticks < 0:
        with pytest.raises(ValueError):
            args.func(args)
    else:
        assert args.func(args) == 0


def test_daemon_stops_on_reported_failure_instead_of_retrying(monkeypatch):
    calls = []
    monkeypatch.setattr(_loop, "_load_config", lambda: {"feeds": {}})
    monkeypatch.setattr(_loop, "run_daemon_tick", lambda **_: calls.append(True) or _report(fatal_llm_error=True))
    with pytest.raises(RuntimeError, match="fatal_llm_error=True"):
        asyncio.run(_loop.run_daemon_loop(max_ticks=2))
    assert calls == [True]


def test_picker_propagates_reader_error_in_both_modes():
    def fail(**_):
        raise RuntimeError("reader failed")

    reader = SimpleNamespace(get_feed_items=fail)
    for size in (None, 3):
        with pytest.raises(RuntimeError, match="reader failed"):
            _tick_phases._pick_unread_batch_round_robin(reader, batch_size=size, feed_library_ids=[1])


def test_dry_cli_bootstrap_disables_background_work(tmp_path, monkeypatch):
    from zotero_summarizer.services import lifecycle

    seen = []
    monkeypatch.setattr(lifecycle, "startup", lambda **kw: seen.append(kw))
    args = build_parser().parse_args(["feeds", "run", "--dry-run", "--project-root", str(tmp_path)])
    cli._bootstrap_feeds_cli(args)
    assert seen == [{"override_model": None, "background": False}]


def test_startup_without_background_does_not_touch_persisted_jobs_or_schedule_work(tmp_path, monkeypatch):
    from zotero_summarizer.services import lifecycle, readiness

    monkeypatch.setattr(lifecycle, "settings", lambda: Settings.load(project_root=tmp_path))
    monkeypatch.setattr(lifecycle, "setup_logging", lambda: None)
    monkeypatch.setattr(lifecycle, "_load_config", lambda *a: SimpleNamespace())
    monkeypatch.setattr(readiness, "all_statuses", lambda: [])
    initialized = []
    for name in ("_init_models", "_init_database", "_init_metadata_clients", "_init_zotero"):
        monkeypatch.setattr(lifecycle, name, lambda *a: None)
    monkeypatch.setattr(lifecycle, "_init_classifier_gate", lambda *a, **kw: initialized.append(kw))

    def forbidden(*args):
        pytest.fail("dry startup must not resume jobs or schedule tasks")

    monkeypatch.setattr(lifecycle, "_load_persisted_jobs", forbidden)
    monkeypatch.setattr(lifecycle, "_schedule_startup_rss_refresh", forbidden)
    lifecycle.startup(background=False)
    assert initialized == [{"background": False}]


@pytest.mark.parametrize("cached", [False, True])
def test_dry_startup_uses_cached_gate_without_training_or_rescoring(tmp_path, monkeypatch, cached):
    from zotero_summarizer.services import lifecycle
    from zotero_summarizer.services.model import classifier, classifier_persistence
    from zotero_summarizer.services.triage import feeds as facade
    from zotero_summarizer.runtime import RuntimeState

    settings = Settings.load(project_root=tmp_path)
    settings.data_dir.mkdir()
    settings.golden_csv_path.touch()
    settings.model_dir.mkdir()
    gate = SimpleNamespace(feature_dim=classifier.FEATURE_DIM, training_metadata={"n_train": 1}, classifier_name="fake", golden_csv_sha256="test")
    monkeypatch.setattr(classifier_persistence, "load_trained", lambda _: gate)
    if cached:
        (settings.model_dir / "fake.joblib").touch()
    config = SimpleNamespace(classifier_gate=SimpleNamespace(enabled=True, model_name="fake", drop_priorities=[]))

    def forbidden(*a, **kw):
        pytest.fail("dry startup must not train or rescore")

    monkeypatch.setattr(facade, "schedule_gate_retrain_async", forbidden)
    monkeypatch.setattr(facade, "schedule_slate_rescore_async", forbidden)
    state = RuntimeState()
    if cached:
        lifecycle._init_classifier_gate(state, config, settings, background=False)
        assert state.classifier_gate is gate
    else:
        with pytest.raises(RuntimeError, match="cached classifier"):
            lifecycle._init_classifier_gate(state, config, settings, background=False)
