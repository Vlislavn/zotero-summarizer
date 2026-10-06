"""Restart boundary: a persisted triage summary becomes the exact Zotero note."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys


from tests._zotero_fixtures import add_feed_item, build_zotero_db
from zotero_summarizer.models import MethodAndCode, SummarizeResponse
from zotero_summarizer.services.triage.feeds._gate import _pack_review_payload
from zotero_summarizer.storage import feeds, repositories


def test_restart_materializes_persisted_summary_verbatim(tmp_path):
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
        repositories.apply_schema(conn)
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

    script = """
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from zotero_summarizer.storage import feeds
from zotero_summarizer.services.library import _review_cache, review_materialize
from zotero_summarizer.services.library.review_summary import pick_stored_summary
from zotero_summarizer.services.triage.feeds import _daily_materialize
from zotero_summarizer.integrations.zotero_write import ZoteroWriter
settings = SimpleNamespace(triage_db_path=Path(sys.argv[1]), zotero_data_dir=Path(sys.argv[2]))
review_materialize.get_settings = lambda: settings
_daily_materialize.get_settings = lambda: settings
ZoteroWriter.is_connector_running = lambda self: False
with feeds.open_triage_conn(settings.triage_db_path) as conn:
    row = dict(conn.execute('SELECT * FROM processed_feed_items WHERE id = ?', (int(sys.argv[3]),)).fetchone())
assert pick_stored_summary(row).model_dump() == json.loads(sys.argv[4])
_review_cache.get_current_review = lambda key: {'needs_pdf': False, 'digest': {'tldr': 'Paper-specific deep review.'}}
key = review_materialize.materialize_row(row, writer=ZoteroWriter(settings.zotero_data_dir), used_keys=set())
print(key)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(triage_db), str(zotero_dir), str(row_id),
         summary.model_dump_json()], check=True, capture_output=True, text=True,
    )
    new_key = result.stdout.strip()

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
