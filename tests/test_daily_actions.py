"""Tests for Today's Stage-1 keep/trash actions (services.daily_actions).

The Zotero materialization + golden CSV writes are I/O boundaries we stub; the
behaviour under test is: the right training labels are recorded, decisions are
set, and feed items are marked read.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zotero_summarizer.services.triage import daily_actions
from zotero_summarizer.services.library import review
from zotero_summarizer.storage import feeds as fs
from zotero_summarizer.storage import repositories as repo
from zotero_summarizer.storage import rss as rss_storage


class _FakeWriter:
    def __init__(self, *a, **k):
        self.read_ids: list[int] = []

    def mark_feed_items_read(self, ids):
        self.read_ids = list(ids)
        return len(ids)


class _FakeSettings:
    def __init__(self, db: Path, zdir: Path):
        self.triage_db_path = db
        self.zotero_data_dir = zdir


def _build_db(tmp_path: Path) -> Path:
    db = tmp_path / "triage_history.db"
    conn = sqlite3.connect(str(db))
    try:
        fs.init_feeds_schema(conn)
        conn.execute(repo._CREATE_LABEL_VERDICTS_TABLE)
        conn.commit()
    finally:
        conn.close()
    return db


def _record(db: Path, feed_item_id: int, *, reading_priority: str | None = None) -> int:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        fs.record_decision(
            conn, run_id="r",
            feed_item={
                "feed_library_id": 1, "item_id": feed_item_id,
                "guid": f"http://arxiv.org/abs/{feed_item_id}", "title": f"P{feed_item_id}",
            },
            decision=fs.DECISION_TRIAGED_PENDING, composite_score=2.0,
            reading_priority=reading_priority,
        )
        conn.commit()
        return int(conn.execute(
            "SELECT id FROM processed_feed_items WHERE feed_item_id=?", (feed_item_id,),
        ).fetchone()["id"])
    finally:
        conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = _build_db(tmp_path)
    fake = _FakeSettings(db, tmp_path / "zot")
    monkeypatch.setattr(daily_actions, "get_settings", lambda: fake)
    monkeypatch.setattr(daily_actions, "ZoteroWriter", _FakeWriter)
    appended: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        review, "append_to_golden",
        lambda row, *, label, note, signal_tier="feed_user_label": appended.append(
            (int(row.get("feed_item_id") or 0), label, signal_tier)
        ) or True,
    )
    return db, appended


def _decision(db: Path, pk: int) -> str:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return str(conn.execute(
            "SELECT decision FROM processed_feed_items WHERE id=?", (pk,),
        ).fetchone()["decision"])
    finally:
        conn.close()


def test_trash_records_dont_read_rejects_and_marks_read(env):
    db, appended = env
    pk = _record(db, 100)
    res = daily_actions.trash([pk])
    assert res["trashed"] == 1
    assert res["marked_read"] == 1
    assert repo.get_label_verdict(db, "feed:100")["user_priority"] == "dont_read"
    assert _decision(db, pk) == fs.DECISION_USER_REJECTED
    # Trash is a confident negative → normal feed_user_label tier (weight 0.5).
    assert (100, "dont_read", "feed_user_label") in appended


def test_add_to_library_materializes_and_labels_should_read(env, monkeypatch):
    db, appended = env
    pk = _record(db, 200)
    materialized: list[int] = []
    monkeypatch.setattr(
        review, "materialize_row",
        lambda row, *, writer, used_keys, reason="x", collection_name="Inbox": materialized.append(int(row["feed_item_id"])) or "KEY1",
    )
    res = daily_actions.add_to_library([pk])
    assert res["added"] == 1
    assert materialized == [200]
    assert repo.get_label_verdict(db, "feed:200")["user_priority"] == "should_read"
    # Add is a soft pre-read interest signal → feed_interest tier (weight 0.3).
    assert (200, "should_read", "feed_interest") in appended


def test_add_to_library_records_original_gate_priority_not_add_label(env, monkeypatch):
    """Regression: add_to_library overrides row["reading_priority"] to
    should_read for the Zotero tag, but the verdict overlay must still record
    the gate/model's ORIGINAL derived priority — not the add label."""
    db, _appended = env
    pk = _record(db, 250, reading_priority="dont_read")  # gate-rejected derived priority
    monkeypatch.setattr(review, "materialize_row", lambda row, **k: "KEY1")
    res = daily_actions.add_to_library([pk])
    assert res["added"] == 1
    verdict = repo.get_label_verdict(db, "feed:250")
    assert verdict["user_priority"] == "should_read"          # the user's add intent
    assert verdict["original_derived_priority"] == "dont_read"  # the gate's verdict, preserved


def test_add_to_library_without_zotero_records_pending_local_approval(env, monkeypatch):
    db, appended = env
    pk = _record(db, 260)

    class _UnavailableWriter:
        def __init__(self, *a, **k):
            raise RuntimeError("zotero unavailable")

    monkeypatch.setattr(daily_actions, "ZoteroWriter", _UnavailableWriter)
    res = daily_actions.add_to_library([pk])

    assert res["added"] == 1
    assert res["pending_sync"] == 1
    assert "zotero unavailable" in res["zotero_sync_error"]
    assert repo.get_label_verdict(db, "feed:260")["user_priority"] == "should_read"
    assert (260, "should_read", "feed_interest") in appended

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT decision, zotero_sync_status, final_outcome
            FROM processed_feed_items
            WHERE id = ?
            """,
            (pk,),
        ).fetchone()
    finally:
        conn.close()
    assert row["decision"] == fs.DECISION_USER_APPROVED
    assert row["zotero_sync_status"] == "pending"
    assert row["final_outcome"] == fs.OUTCOME_KEPT_UNREAD_APP


def test_add_to_library_runs_real_materialize_row(tmp_path, monkeypatch):
    """Regression: the REAL ``review.materialize_row`` path must succeed.

    Retiring the ``zs:<priority>`` write made ``feeds._tags_from_row`` keyword-
    only, but this caller still passed ``row`` positionally → ``add_to_library``
    silently returned ``added: 0`` (the "Added 0 papers" the user hit). Every
    OTHER add_to_library test mocks ``materialize_row``, so only a test that runs
    the real body — with a capturing Zotero writer — catches it.
    """
    db = _build_db(tmp_path)
    pk = _record(db, 400, reading_priority="dont_read")
    fake = _FakeSettings(db, tmp_path / "zot")
    captured: dict = {}

    class _MatWriter:
        def __init__(self, *a, **k):
            pass

        def apply_feed_materialization(self, **kw):
            captured.update(kw)
            return {"item_key": kw["new_item_key"]}

    monkeypatch.setattr(daily_actions, "get_settings", lambda: fake)
    monkeypatch.setattr(daily_actions, "ZoteroWriter", _MatWriter)
    monkeypatch.setattr(review, "get_settings", lambda: fake)            # materialize_row._conn
    monkeypatch.setattr(review, "append_to_golden", lambda *a, **k: True)
    monkeypatch.setattr(daily_actions, "_attach_fulltext_best_effort", lambda keys: {"attached": 0})

    res = daily_actions.add_to_library([pk])
    assert res["added"] == 1, res.get("failed")          # NOT the silent "added: 0"
    assert isinstance(captured.get("tags"), list)        # _tags_from_row ran without a positional crash


def test_trash_app_rss_row_marks_local_item_read(env, monkeypatch):
    db, _appended = env
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        fs.init_feeds_schema(conn)
        feed_id = rss_storage.upsert_rss_feed(
            conn,
            name="App Feed",
            url="https://example.com/rss",
        )
        rss_item_id, _inserted = rss_storage.upsert_rss_item(
            conn,
            rss_feed_id=feed_id,
            item={
                "feed_library_id": feed_id,
                "item_id": 0,
                "guid": "app-rss-trash-guid",
                "title": "App RSS Paper",
            },
        )
        fs.record_decision(
            conn,
            run_id="r",
            feed_item={
                "feed_library_id": feed_id,
                "item_id": rss_item_id,
                "source_type": "app_rss",
                "guid": "app-rss-trash-guid",
                "title": "App RSS Paper",
            },
            decision=fs.DECISION_TRIAGED_PENDING,
        )
        conn.commit()
        pk = int(conn.execute(
            "SELECT id FROM processed_feed_items WHERE feed_item_id = ?",
            (rss_item_id,),
        ).fetchone()["id"])
    finally:
        conn.close()

    class _UnavailableWriter:
        def __init__(self, *a, **k):
            raise RuntimeError("zotero unavailable")

    monkeypatch.setattr(daily_actions, "ZoteroWriter", _UnavailableWriter)
    res = daily_actions.trash([pk])

    assert res["trashed"] == 1
    assert res["marked_read"] == 1
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        read_at = conn.execute(
            "SELECT read_at FROM rss_items WHERE id = ?",
            (rss_item_id,),
        ).fetchone()["read_at"]
    finally:
        conn.close()
    assert read_at


def test_batch_handles_multiple_ids(env, monkeypatch):
    db, appended = env
    monkeypatch.setattr(review, "materialize_row", lambda row, **k: "K")
    pks = [_record(db, fid) for fid in (301, 302, 303)]
    res = daily_actions.add_to_library(pks)
    assert res["added"] == 3
    assert {a[1] for a in appended} == {"should_read"}
    assert {a[2] for a in appended} == {"feed_interest"}  # all soft-tiered


def test_append_to_golden_writes_signal_tier_to_csv(tmp_path, monkeypatch):
    """End-to-end (no Zotero): the tier reaches the golden CSV column, which is
    what label_weights reads to assign the 0.3 weight."""
    import csv as _csv
    import dataclasses
    from types import SimpleNamespace
    from zotero_summarizer.services.golden.goldenset import GoldenSample

    fields = [f.name for f in dataclasses.fields(GoldenSample)]
    csv_path = tmp_path / "golden.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        _csv.DictWriter(f, fieldnames=fields).writeheader()

    monkeypatch.setattr(review, "_fetch_feed_metadata", lambda **k: {})
    monkeypatch.setattr(review, "get_settings", lambda: SimpleNamespace(project_root=tmp_path))

    review.append_to_golden(
        {"feed_item_id": 777, "feed_library_id": 1, "title": "T", "doi": ""},
        label="should_read", note="added from Today",
        signal_tier="feed_interest", golden_csv_path=csv_path,
    )
    review.append_to_golden(  # default tier path (e.g. trash / relabel)
        {"feed_item_id": 778, "feed_library_id": 1, "title": "T2", "doi": ""},
        label="dont_read", note="trashed", golden_csv_path=csv_path,
    )

    with csv_path.open(encoding="utf-8") as f:
        rows = {r["item_key"]: r for r in _csv.DictReader(f)}
    assert rows["feed:777"]["gold_signal_tier"] == "feed_interest"
    assert rows["feed:777"]["gold_priority_final"] == "should_read"
    assert rows["feed:778"]["gold_signal_tier"] == "feed_user_label"  # default preserved


def test_carry_renders_best_effort_rebuilds_only_completed_feed_renders(monkeypatch):
    """Render persistence on Add: only a feed paper that ALREADY had a completed render
    rebuilds under its new Zotero key; running/absent/empty are skipped. Best-effort."""
    from zotero_summarizer.services.library import paper_render

    built: list[str] = []
    monkeypatch.setattr(paper_render, "start_build", lambda key, **kw: built.append(key))
    states = {"feed:d:DONE": {"status": "completed"}, "feed:d:RUN": {"status": "running"}}
    monkeypatch.setattr(paper_render, "_read_state", lambda key: states.get(key))

    daily_actions._carry_renders_best_effort([
        ("feed:d:DONE", "ZK1"),   # completed → rebuild under the new key
        ("feed:d:RUN", "ZK2"),    # not completed → skip
        ("feed:d:NONE", "ZK3"),   # no render state → skip
        ("", "ZK4"),              # no feed key → skip
    ])
    assert built == ["ZK1"]
