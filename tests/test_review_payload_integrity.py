"""Review uses the same fail-fast stored-payload boundary as Today."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_review_workflow import _insert_awaiting, patched_settings  # noqa: F401
from zotero_summarizer.api.routes import review as routes
from zotero_summarizer.services.library import review, review_summary
from zotero_summarizer.storage import feeds as fs


@pytest.mark.parametrize("state", ["awaiting_review", "gate_rejected"])
@pytest.mark.parametrize("sort", ["recent", "border"])
@pytest.mark.parametrize("payload", ['{"summary":', "null"])
def test_corrupt_queue_fails_with_row_identity_without_mutation(patched_settings, state, sort, payload):
    with fs.open_triage_conn(patched_settings / "triage.db") as conn, conn:
        _insert_awaiting(conn)
        corrupt_id = _insert_awaiting(conn, feed_item_id=102)
        conn.execute("UPDATE processed_feed_items SET decision = ?", (state,))
        conn.execute("UPDATE processed_feed_items SET shap_contribs_json = ? WHERE id = ?",
                     (payload, corrupt_id))
        before = conn.execute("SELECT * FROM processed_feed_items ORDER BY id").fetchall()
    golden_before = (patched_settings / "zotero-summarizer-golden.csv").read_bytes()
    app = FastAPI()
    app.include_router(routes.router)
    url = f"/api/feeds/review?state={state}&sort={sort}"

    with TestClient(app) as client, pytest.raises(ValueError, match=f"row id={corrupt_id}.*shap_contribs_json"):
        client.get(url)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get(url).status_code == 500

    with fs.open_triage_conn(patched_settings / "triage.db") as conn:
        assert conn.execute("SELECT * FROM processed_feed_items ORDER BY id").fetchall() == before
        assert conn.execute("SELECT COUNT(*) FROM label_verdicts").fetchone()[0] == 0
    assert (patched_settings / "zotero-summarizer-golden.csv").read_bytes() == golden_before


@pytest.mark.parametrize("payload", [None, "", "  ", "{}"])
def test_absent_legacy_payload_is_not_corruption(patched_settings, payload):
    with fs.open_triage_conn(patched_settings / "triage.db") as conn, conn:
        row_id = _insert_awaiting(conn)
        conn.execute("UPDATE processed_feed_items SET shap_contribs_json = ?", (payload,))
    app = FastAPI()
    app.include_router(routes.router)

    with TestClient(app) as client:
        response = client.get("/api/feeds/review")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    row = response.json()["items"][0]
    assert row["id"] == row_id
    assert [row[key] for key in ("shap", "aux_context", "summary", "audit_pick")] == [None, None, None, False]
    assert review.pick_stored_summary(row) is None


@pytest.mark.parametrize("payload", ['{"summary":', "null", "[]", '"text"', "42"])
@pytest.mark.parametrize("consumer", ["stored", "promoted"])
def test_summary_readers_share_strict_payload_validation(payload, consumer):
    row = {"id": 91, "shap_contribs_json": payload}

    with pytest.raises(ValueError, match="row id=91.*shap_contribs_json") as error:
        if consumer == "stored":
            review.pick_stored_summary(row)
        else:
            review_summary._build_summary_for_queue(row, "must_read")

    if payload == '{"summary":':
        assert isinstance(error.value.__cause__, json.JSONDecodeError)
