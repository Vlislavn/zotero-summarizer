"""Forward-only integration of the two isolated version-12 schema components."""
from contextlib import closing, contextmanager

import pytest

from zotero_summarizer.storage import migrations, pending_actions, repositories
from zotero_summarizer.storage import research_feed_telegram as telegram


@contextmanager
def _connection(path):
    with closing(repositories._connect_to(path)) as conn:
        with conn:
            yield conn


def _version_twelve(path, lineage):
    assert migrations.run_migrations(path, "triage", [m for m in migrations.TRIAGE_MIGRATIONS if m.version <= 11]) == 11
    with _connection(path) as conn:
        conn.execute("INSERT INTO rss_feeds(id,name,url) VALUES (1,'preserved','https://example.invalid/feed')")
        if lineage == "generated":
            pending_actions.apply_schema(conn)
        elif lineage == "live":
            telegram.apply_live_schema(conn)
        elif lineage == "partial_pending":
            conn.execute("ALTER TABLE pending_changes ADD COLUMN proposal_json TEXT")
        elif lineage == "partial_live":
            conn.execute("ALTER TABLE research_feed_telegram_channels ADD COLUMN initial_window_committed INTEGER")
        else:
            raise AssertionError(lineage)
        migrations._record_version(conn, "triage", 12)


@pytest.mark.parametrize("lineage", ["generated", "live"])
def test_complete_isolated_v12_lineages_upgrade_to_both_contracts_without_data_loss(tmp_path, lineage):
    path = tmp_path / "triage.sqlite"
    _version_twelve(path, lineage)
    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS) == 13
    with _connection(path) as conn:
        assert {"proposal_json", "audit_json", "backup_path"} <= repositories.table_columns(conn, "pending_changes")
        assert "initial_window_committed" in repositories.table_columns(conn, "research_feed_telegram_channels")
        assert conn.execute("SELECT COUNT(*) FROM research_feed_telegram_account").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM research_feed_telegram_bindings").fetchone()[0] == 0
        assert conn.execute("SELECT name FROM rss_feeds WHERE id=1").fetchone()[0] == "preserved"
        before = list(conn.iterdump())
    assert migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS) == 13
    with _connection(path) as conn:
        assert list(conn.iterdump()) == before


@pytest.mark.parametrize("lineage", ["partial_pending", "partial_live"])
def test_partial_v12_footprint_fails_without_publishing_a_new_schema_version(tmp_path, lineage):
    path = tmp_path / "triage.sqlite"
    _version_twelve(path, lineage)
    with pytest.raises(ValueError, match="incomplete experimental version-12 schema footprint"):
        migrations.run_migrations(path, "triage", migrations.TRIAGE_MIGRATIONS)
    with _connection(path) as conn:
        assert conn.execute("SELECT version FROM schema_migrations WHERE namespace='triage'").fetchone()[0] == 12
        assert conn.execute("SELECT name FROM rss_feeds WHERE id=1").fetchone()[0] == "preserved"
