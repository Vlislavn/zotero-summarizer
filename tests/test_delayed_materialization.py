"""Restart boundary: a persisted triage summary becomes the exact Zotero note."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests._zotero_fixtures import add_feed_item, build_zotero_db
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
from zotero_summarizer.models import MethodAndCode, SummarizeResponse
from zotero_summarizer.services.library import review_materialize
from zotero_summarizer.services.triage.feeds import _daily_materialize
from zotero_summarizer.services.triage.feeds._gate import _pack_review_payload
from zotero_summarizer.storage import feeds


class _Settings:
    def __init__(self, triage_db_path: Path, zotero_data_dir: Path):
        self.triage_db_path = triage_db_path
        self.zotero_data_dir = zotero_data_dir


def test_restart_materializes_persisted_summary_verbatim(tmp_path, monkeypatch):
    triage_db = tmp_path / "triage.db"
    zotero_dir = tmp_path / "zotero"
    zotero_db = build_zotero_db(zotero_dir)
    add_feed_item(
        zotero_db, feed_library_id=2, item_id=400, guid="restart-paper",
        title="Restart-safe paper", abstract="Original feed abstract.",
    )
    summary = SummarizeResponse(
        executive_summary="Restart-specific overview.",
        key_findings=["Restart-specific finding."],
        methods="Restart-specific method.",
        limitations="Restart-specific limitation.",
        relevance_to_research="Restart-specific relevance.",
        method_and_code=MethodAndCode(
            what_it_does="Restart-specific reusable method.",
            artifacts=["https://example.org/restart-code"],
        ),
        relevance_score=4, composite_relevance_score=4.2,
        reading_priority="should_read", triage_rationale="Strong fit.",
    )
    payload = _pack_review_payload({}, summary)
    assert json.loads(payload or "{}")["summary_schema_version"] == 1

    with feeds.open_triage_conn(triage_db) as conn:
        feeds.init_feeds_schema(conn)
        row_id = feeds.record_decision(
            conn, run_id="before-restart",
            feed_item={
                "feed_library_id": 2, "item_id": 400, "guid": "restart-paper",
                "title": "Restart-safe paper", "abstract": "Original feed abstract.",
            },
            decision=feeds.DECISION_USER_APPROVED,
            composite_score=4.2, reading_priority="should_read",
            shap_contribs_json=payload,
        )
        conn.commit()

    # A new connection is the process-restart boundary: no in-memory summary survives.
    with feeds.open_triage_conn(triage_db) as conn:
        row = dict(conn.execute(
            "SELECT * FROM processed_feed_items WHERE id = ?", (row_id,),
        ).fetchone())

    settings = _Settings(triage_db, zotero_dir)
    monkeypatch.setattr(review_materialize, "get_settings", lambda: settings)
    monkeypatch.setattr(_daily_materialize, "get_settings", lambda: settings)
    monkeypatch.setattr(ZoteroWriter, "is_connector_running", lambda self: False)
    new_key = review_materialize.materialize_row(
        row, writer=ZoteroWriter(zotero_dir), used_keys=set(),
    )

    with sqlite3.connect(zotero_db) as conn:
        note = conn.execute(
            """
            SELECT n.note FROM itemNotes n
            JOIN items parent ON parent.itemID = n.parentItemID
            WHERE parent.key = ?
            """,
            (new_key,),
        ).fetchone()[0]
    for expected in (
        "Restart-specific overview", "Restart-specific finding",
        "Restart-specific method", "Restart-specific limitation",
        "Restart-specific relevance", "Restart-specific reusable method",
        "https://example.org/restart-code",
    ):
        assert expected in note

    with feeds.open_triage_conn(triage_db) as conn:
        persisted = conn.execute(
            "SELECT decision, materialized_zotero_key FROM processed_feed_items WHERE id = ?",
            (row_id,),
        ).fetchone()
    assert persisted["decision"] == feeds.DECISION_SELECTED
    assert persisted["materialized_zotero_key"] == new_key
