"""Scripted refinement through the existing SQLite/reload/note path; no live model."""
import json
from unittest.mock import Mock

import pytest

from test_method_source_admission import MEMBER, INVENTED, config, receipt_path, wire_pipeline
from zotero_summarizer.models import SummarizeRequest
from zotero_summarizer.services.library.review_summary import pick_stored_summary
from zotero_summarizer.services.triage import summarization
from zotero_summarizer.services.triage.feeds._gate import _pack_review_payload
from zotero_summarizer.services.zotero.pending import build_triage_note_html
from zotero_summarizer.storage import feeds, repositories


def test_admitted_summary_survives_storage_reopen_and_real_note_renderer(monkeypatch, config, receipt_path):
    source = "Our contribution code <" + MEMBER + ">. Background <https://example.org/cited-repo>."
    client = Mock()
    wire_pipeline(monkeypatch, config, client, source)
    client.prompt.return_value = json.dumps({
        "executive_summary": "Authors describe a reusable comparison procedure.",
        "method_and_code": {
            "what_it_does": "Compares source observations.",
            "what_is_new": "Authors propose the comparison procedure.",
            "how_it_works": ["Compare observations without assuming causation."],
            "evaluation": "Authors report an association, not a causal effect.",
            "artifacts": [MEMBER, INVENTED, MEMBER],
            "how_i_could_use_it": "Proposed adaptation: compare my own observations.",
        },
    })
    response = summarization.run_abstract_pipeline(SummarizeRequest(title="Owned fixture", abstract=source))
    path = receipt_path.parent / "triage.db"
    with feeds.open_triage_conn(path) as conn:
        repositories.apply_schema(conn)
        row_id = feeds.record_decision(
            conn, run_id="owned-fixture", feed_item={"feed_library_id": 2, "item_id": 1,
                "guid": "owned-fixture", "title": "Owned fixture"},
            decision=feeds.DECISION_AWAITING_REVIEW,
            shap_contribs_json=_pack_review_payload({}, response),
        )
        conn.commit()
    with feeds.open_triage_conn(path) as conn:
        row = dict(conn.execute("SELECT * FROM processed_feed_items WHERE id = ?", (row_id,)).fetchone())
    restored = pick_stored_summary(row)
    note = build_triage_note_html("Owned fixture", restored)

    assert restored == response
    assert restored.method_and_code.artifacts == [MEMBER, MEMBER]
    assert INVENTED not in row["shap_contribs_json"] and INVENTED not in note
    assert "cited-repo" not in note  # Original citation is not automatically promoted.
    for value in (MEMBER, "Authors propose", "not a causal effect", "Proposed adaptation"):
        assert value in note
    assert json.loads(receipt_path.read_text())["candidate"] == INVENTED
    client.prompt.assert_called_once()
    client.pydantic_prompt.assert_called_once()


@pytest.mark.parametrize("method", [None, {
    "what_it_does": "A reusable method without released software.",
    "how_it_works": ["Compare source observations."], "artifacts": [],
}])
def test_no_code_and_null_decisions_keep_their_existing_meaning(monkeypatch, config, receipt_path, method):
    client = Mock()
    wire_pipeline(monkeypatch, config, client, "Available source without code links.")
    client.prompt.return_value = json.dumps({"executive_summary": "Scripted fixture", "method_and_code": method})

    response = summarization.run_abstract_pipeline(SummarizeRequest(
        title="Owned fixture", abstract="Available source without code links."))
    note = build_triage_note_html("Owned fixture", response)

    assert (response.method_and_code is None) is (method is None)
    assert ("Method and code" in note) is (method is not None)
    if method:
        assert response.method_and_code.artifacts == []
        assert method["what_it_does"] in note
    assert not receipt_path.exists()
    client.prompt.assert_called_once()
    client.pydantic_prompt.assert_called_once()


def test_legacy_payload_without_method_or_schema_version_still_reloads():
    row = {"shap_contribs_json": json.dumps({"summary": {
        "executive_summary": "Legacy summary", "relevance_score": 2, "triage_rationale": "Legacy rationale",
    }})}

    restored = pick_stored_summary(row)

    assert restored.method_and_code is None
    assert "Method and code" not in build_triage_note_html("Legacy fixture", restored)
