"""Populated historical sync stores upgrade without weakening protections."""

import sqlite3
from contextlib import closing

import pytest

from zotero_summarizer.storage import migrations, repositories, review_questions

# Frozen historical v2 declaration: deliberately not imported from current schema.
OLD_SYNC_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_changes (
    revision    INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key   TEXT NOT NULL,
    field      TEXT NOT NULL CHECK (field IN ('verdict', 'review_note')),
    value      TEXT,
    comment    TEXT,
    source     TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sync_changes_item_field
    ON sync_changes(item_key, field, revision);
CREATE TABLE IF NOT EXISTS sync_mutations (
    mutation_id TEXT PRIMARY KEY,
    device_id   TEXT NOT NULL,
    item_key    TEXT NOT NULL,
    field       TEXT NOT NULL,
    operation   TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    resolves_mutation_id TEXT,
    request_json TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sync_mutations_resolution
    ON sync_mutations(resolves_mutation_id);
CREATE TRIGGER IF NOT EXISTS sync_label_insert AFTER INSERT ON label_verdicts BEGIN
  INSERT INTO sync_changes(item_key, field, value, comment, source)
  VALUES (NEW.item_key, 'verdict', NEW.user_priority, NEW.comment, NEW.source);
END;
CREATE TRIGGER IF NOT EXISTS sync_label_update AFTER UPDATE ON label_verdicts
WHEN OLD.user_priority IS NOT NEW.user_priority
  OR OLD.comment IS NOT NEW.comment OR OLD.source IS NOT NEW.source BEGIN
  INSERT INTO sync_changes(item_key, field, value, comment, source)
  VALUES (NEW.item_key, 'verdict', NEW.user_priority, NEW.comment, NEW.source);
END;
CREATE TRIGGER IF NOT EXISTS sync_label_delete AFTER DELETE ON label_verdicts BEGIN
  INSERT INTO sync_changes(item_key, field, value, comment, source)
  VALUES (OLD.item_key, 'verdict', NULL, NULL, OLD.source);
END;
CREATE TRIGGER IF NOT EXISTS sync_note_insert AFTER INSERT ON review_notes BEGIN
  INSERT INTO sync_changes(item_key, field, value)
  VALUES (NEW.item_key, 'review_note', NEW.note);
END;
CREATE TRIGGER IF NOT EXISTS sync_note_update AFTER UPDATE ON review_notes
WHEN OLD.note IS NOT NEW.note BEGIN
  INSERT INTO sync_changes(item_key, field, value)
  VALUES (NEW.item_key, 'review_note', NEW.note);
END;
CREATE TRIGGER IF NOT EXISTS sync_note_delete AFTER DELETE ON review_notes BEGIN
  INSERT INTO sync_changes(item_key, field, value)
  VALUES (OLD.item_key, 'review_note', NULL);
END;
"""


def _historical_sync(conn):
    conn.executescript(OLD_SYNC_SCHEMA)


def _historical_database(tmp_path, version):
    path = tmp_path / f"v{version}.sqlite3"
    steps = [migrations.Migration(2, "offline_mutation_sync", _historical_sync) if m.version == 2 else m
             for m in migrations.TRIAGE_MIGRATIONS if m.version <= version]
    migrations.run_migrations(path, "triage", steps)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO review_notes VALUES ('PAPER001', 'Existing manual', '2026-01-01')")
        conn.execute("INSERT INTO label_verdicts(item_key, original_derived_priority, user_priority, comment, created_at) "
                     "VALUES ('PAPER001', 'must_read', 'should_read', 'Existing label', '2026-01-02')")
        conn.execute("DELETE FROM label_verdicts")
        deletion = conn.execute("SELECT MAX(revision) FROM sync_changes").fetchone()[0]
        conn.execute("INSERT INTO label_mirror_receipts VALUES (?)", (deletion,))
        conn.execute("INSERT INTO sync_mutations VALUES "
                     "('mutation', 'device', 'PAPER001', 'verdict', 'delete', 2, NULL, "
                     "'{\"immutable\":true}', '{\"status\":\"applied\",\"applied_revision\":3}', 'old', 'old')")
        conn.execute("INSERT INTO sync_changes(revision,item_key,field,value) VALUES (100, 'HIGHWATER', 'review_note', 'gone')")
        conn.execute("DELETE FROM sync_changes WHERE revision = 100")
        if version == 9:
            for submission, time in [(2, "2026-01-04"), (1, "2026-01-03")]:
                conn.execute("INSERT INTO review_questions VALUES (?, 'PAPER001', ?, 'typed', NULL, ?)",
                             (f"00000000-0000-4000-8000-{submission:012d}", f"Question {submission}", time))
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    return path


@pytest.fixture
def enforce_foreign_keys(monkeypatch):
    connect = repositories._connect_to

    def foreign_keys_on(path):
        conn = connect(path)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(repositories, "_connect_to", foreign_keys_on)


def _rows(conn, table):
    return [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


@pytest.mark.parametrize("version", [8, 9])
def test_populated_upgrade_preserves_history_receipts_triggers_and_highwater(tmp_path, enforce_foreign_keys, version):
    path = _historical_database(tmp_path, version)
    preserved = ("sync_changes", "sync_mutations", "review_notes", "label_verdicts", "label_mirror_receipts")
    with closing(repositories._connect_to(path)) as conn:
        before = {table: _rows(conn, table) for table in preserved}
        triggers = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'"))
        assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name='sync_changes'").fetchone()[0] == 100

    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS) == migrations.SCHEMA_VERSION

    with closing(repositories._connect_to(path)) as conn, conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in preserved:
            rows = _rows(conn, table)
            assert rows[:len(before[table])] == before[table]
            if table != "sync_changes":
                assert rows == before[table]
        after_triggers = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'"))
        assert all(after_triggers[name] == sql for name, sql in triggers.items())
        questions = conn.execute("SELECT revision, value FROM sync_changes WHERE field='review_question' ORDER BY revision").fetchall()
        if version == 9:
            assert [row[0] for row in questions] == [101, 102]
            assert [row[1] for row in questions] == [f"00000000-0000-4000-8000-{i:012d}" for i in [1, 2]]
        else:
            assert questions == []
        assert conn.execute("SELECT COUNT(*) FROM review_note_mirror_intents").fetchone()[0] == 1
        saved, created = review_questions.save(conn, dict(submission_id="00000000-0000-4000-8000-000000000003",
                                                         item_key="PAPER001", question="New", origin="typed"))
        assert created and conn.execute("SELECT MAX(revision) FROM sync_changes").fetchone()[0] > 100
        if version == 9:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute("UPDATE review_questions SET question='changed' WHERE submission_id=?", (saved["submission_id"],))
        snapshot = {table: _rows(conn, table) for table in (*preserved, "review_questions", "review_note_mirror_intents")}
    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS) == migrations.SCHEMA_VERSION
    with closing(repositories._connect_to(path)) as conn, conn:
        assert {table: _rows(conn, table) for table in snapshot} == snapshot
        before_revision = conn.execute("SELECT MAX(revision) FROM sync_changes").fetchone()[0]
        conn.execute("INSERT INTO label_verdicts(item_key, original_derived_priority, user_priority, comment, created_at) "
                     "VALUES ('PAPER001', 'must_read', 'should_read', 'unchanged', 'now')")
        conn.execute("UPDATE label_verdicts SET original_derived_priority='unknown'")
        conn.execute("UPDATE label_verdicts SET created_at='timestamp-only'")
        conn.execute("DELETE FROM label_verdicts")
        conn.execute("UPDATE review_notes SET note='Existing manual'")
        conn.execute("UPDATE review_notes SET note='changed'")
        conn.execute("DELETE FROM review_notes")
        tail = conn.execute("SELECT field, value FROM sync_changes WHERE revision > ? ORDER BY revision", (before_revision,)).fetchall()
        assert [tuple(row) for row in tail] == [("verdict", "should_read"), ("verdict", "should_read"),
                                               ("verdict", None), ("review_note", "changed"), ("review_note", None)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v10_failure_rolls_back_rebuild_backfill_and_marker(tmp_path, enforce_foreign_keys):
    path = _historical_database(tmp_path, 9)
    with closing(repositories._connect_to(path)) as conn, conn:
        conn.execute("CREATE TRIGGER reject_question_journal BEFORE INSERT ON sync_changes "
                     "WHEN NEW.field = 'review_question' BEGIN SELECT RAISE(ABORT, 'injected migration failure'); END")
        before_schema = [tuple(row) for row in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        before = {table: _rows(conn, table) for table in ("sync_changes", "sync_mutations", "label_mirror_receipts", "schema_migrations", "review_questions", "sqlite_sequence")}
    with pytest.raises(sqlite3.IntegrityError, match="injected migration failure"):
        migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS)
    with closing(repositories._connect_to(path)) as conn:
        assert [tuple(row) for row in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")] == before_schema
        assert {table: _rows(conn, table) for table in before} == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_new_and_upgraded_databases_converge_and_corpus_alignment_is_named(tmp_path, enforce_foreign_keys):
    upgraded = _historical_database(tmp_path, 9)
    fresh = tmp_path / "fresh.sqlite3"
    for path in [upgraded, fresh]:
        assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS) == migrations.SCHEMA_VERSION
    schemas = []
    for path in [upgraded, fresh]:
        with closing(repositories._connect_to(path)) as conn:
            schemas.append([tuple(row) for row in conn.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")])
    assert schemas[0] == schemas[1]
    alignment = next(m for m in migrations.CORPUS_MIGRATIONS if m.name == "review_note_projection_alignment")
    assert alignment.version == 10 and alignment.name == "review_note_projection_alignment"
    corpus = tmp_path / "corpus.sqlite3"
    assert migrations.run_migrations(corpus, "corpus", [alignment]) == 10
    with sqlite3.connect(corpus) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("schema_migrations",)]
