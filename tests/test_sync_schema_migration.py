"""The real v2 runner owns schema, data and version-marker atomicity."""

import sqlite3
from contextlib import closing

import pytest

from zotero_summarizer.storage import migrations, repositories
from zotero_summarizer.storage._repo_sync import apply_sync_schema


def _v1_database(tmp_path):
    path = tmp_path / "v1.sqlite3"
    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS[:1]) == 1
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO review_notes VALUES ('PAPER001', 'original note', 'old')")
        conn.execute(
            "INSERT INTO label_verdicts "
            "(item_key, original_derived_priority, user_priority, comment, created_at) "
            "VALUES ('PAPER001', 'must_read', 'should_read', 'original comment', 'old')"
        )
    return path


def _snapshot(conn):
    return {
        "schema": [tuple(row) for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )],
        **{table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
           for table in ("review_notes", "label_verdicts", "schema_migrations", "sqlite_sequence")},
    }


@pytest.mark.parametrize("failure_at", ["callback", "marker"])
def test_v2_failure_restores_schema_data_and_version(tmp_path, monkeypatch, failure_at):
    path = _v1_database(tmp_path)
    with closing(repositories._connect_to(path)) as conn:
        before = _snapshot(conn)
    v2 = migrations.TRIAGE_MIGRATIONS[1]
    transaction_after_schema = []

    def apply_then_fail(conn):
        conn.execute("UPDATE review_notes SET note='before schema'")
        v2.apply(conn)
        transaction_after_schema.append(conn.in_transaction)
        assert conn.execute("SELECT COUNT(*) FROM sync_changes").fetchone()[0] == 0
        conn.execute("UPDATE review_notes SET note='after schema'")
        assert conn.execute("SELECT value FROM sync_changes").fetchone()[0] == "after schema"
        if failure_at == "callback":
            raise RuntimeError("injected v2 failure")

    def fail_marker(_conn, namespace, version):
        assert (namespace, version) == ("triage", 2)
        raise RuntimeError("injected v2 failure")

    if failure_at == "marker":
        monkeypatch.setattr(migrations, "_record_version", fail_marker)
    steps = [migrations.Migration(v2.version, v2.name, apply_then_fail)]

    with pytest.raises(RuntimeError, match="injected v2 failure"):
        migrations.run_migrations(path, "triage", steps)

    with closing(repositories._connect_to(path)) as conn:
        assert conn.execute("SELECT version FROM schema_migrations").fetchone()[0] == 1
        assert _snapshot(conn) == before
    assert transaction_after_schema == [True]


def test_v2_upgrade_and_replay_preserve_data_and_all_trigger_effects(tmp_path):
    path = _v1_database(tmp_path)
    steps = migrations.TRIAGE_MIGRATIONS[:2]

    assert migrations.run_migrations(path, "triage", steps) == 2

    with closing(repositories._connect_to(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_changes").fetchone()[0] == 0
        assert conn.execute("SELECT note FROM review_notes").fetchone()[0] == "original note"
        assert conn.execute("SELECT comment FROM label_verdicts").fetchone()[0] == "original comment"
        before_replay = _snapshot(conn)
    assert migrations.run_migrations(path, "triage", steps) == 2
    with closing(repositories._connect_to(path)) as conn, conn:
        assert _snapshot(conn) == before_replay
        conn.execute("BEGIN IMMEDIATE")
        apply_sync_schema(conn)
        assert conn.in_transaction
        assert _snapshot(conn) == before_replay
        conn.execute(
            "INSERT INTO label_verdicts "
            "(item_key, original_derived_priority, user_priority, comment, created_at) "
            "VALUES ('NEW', 'must_read', 'should_read', 'comment; intact', 'now')"
        )
        conn.execute("UPDATE label_verdicts SET created_at='timestamp only' WHERE item_key='NEW'")
        conn.execute("UPDATE label_verdicts SET user_priority='must_read' WHERE item_key='NEW'")
        conn.execute("DELETE FROM label_verdicts WHERE item_key='NEW'")
        conn.execute("INSERT INTO review_notes VALUES ('NEW', 'note; intact', 'now')")
        conn.execute("UPDATE review_notes SET note='note; intact' WHERE item_key='NEW'")
        conn.execute("UPDATE review_notes SET note='changed' WHERE item_key='NEW'")
        conn.execute("DELETE FROM review_notes WHERE item_key='NEW'")
        changes = [tuple(row) for row in conn.execute(
            "SELECT revision,item_key,field,value,comment,source FROM sync_changes ORDER BY revision"
        )]
        assert changes == [
            (1, "NEW", "verdict", "should_read", "comment; intact", "user"),
            (2, "NEW", "verdict", "must_read", "comment; intact", "user"),
            (3, "NEW", "verdict", None, None, "user"),
            (4, "NEW", "review_note", "note; intact", None, None),
            (5, "NEW", "review_note", "changed", None, None),
            (6, "NEW", "review_note", None, None, None),
        ]
    assert migrations.run_migrations(path, "triage", steps) == 2
    with closing(repositories._connect_to(path)) as conn:
        assert [tuple(row) for row in conn.execute(
            "SELECT revision,item_key,field,value,comment,source FROM sync_changes ORDER BY revision"
        )] == changes
        assert conn.execute("SELECT COUNT(*) FROM sync_mutations").fetchone()[0] == 0


def test_migration_versions_remain_ordered_and_aligned():
    assert migrations.SCHEMA_VERSION == 13
    for steps in (migrations.TRIAGE_MIGRATIONS, migrations.CORPUS_MIGRATIONS):
        assert [step.version for step in steps] == list(range(1, 14))
