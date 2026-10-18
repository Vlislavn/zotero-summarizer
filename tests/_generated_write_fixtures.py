"""Owned native target + queue for generated-note staging, without a live library."""
from tests._zotero_fixtures import build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.services.zotero import zotero
from zotero_summarizer.storage import repositories


def install_generated_writer(tmp_path, monkeypatch):
    monkeypatch.setattr(repositories, "DB_PATH", tmp_path / "triage.db")
    repositories.init_db()
    db = build_zotero_db(tmp_path / "zotero")
    writer = ZoteroWriter(db.parent)
    monkeypatch.setattr(writer, "is_connector_running", lambda: False)
    monkeypatch.setattr(zotero, "get_zotero_writer_or_raise", lambda: writer)
    return db, writer
