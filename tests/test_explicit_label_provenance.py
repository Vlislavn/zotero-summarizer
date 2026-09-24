"""Explicit label provenance agrees with export, not a fresh emoji-only guess."""
import csv

import pytest
from fastapi.testclient import TestClient

from tests._zotero_fixtures import add_library_item, add_tag_to_item, build_zotero_db
from tests.test_golden_input_boundaries import _app
from zotero_summarizer.api.routes import _golden_helpers
from zotero_summarizer.domain import PRIORITY_TO_RELEVANCE
from zotero_summarizer.services.golden import goldenset, label_provenance as lp


@pytest.mark.parametrize("priority", list(PRIORITY_TO_RELEVANCE))
@pytest.mark.parametrize("trash", [False, True])
@pytest.mark.parametrize("veto", [False, True])
def test_explicit_label_precedes_all_engagement_signals(priority, trash, veto):
    tags = [f"label:{priority}", "🧠"] + (["👎"] if veto else [])
    expected = goldenset._infer_label(tags=tags, in_trash=trash, note_count=5,
                                    annotation_count=20, days_since_added=900)
    result = lp.compute_provenance(item_key="PAPER001", title="Paper", tags=tags,
                                   in_trash=trash, user_note_count=5, annotation_count=20,
                                   days_since_added=900, persisted_priority=priority)
    assert (result.derived_priority, result.derived_strength, result.derived_score) == expected[:3]
    assert lp.provenance_to_dict(result)["short_circuits"] == {
        "explicit_label": priority, "in_trash_override": False, "hard_veto_emojis": [],
    }
    assert result.emoji_contributions == []
    assert result.engagement_sum_decayed == 0
    assert result.flags == []
    assert result.is_manual_override is False


@pytest.mark.parametrize("priority", list(PRIORITY_TO_RELEVANCE))
@pytest.mark.parametrize("edited", [False, True])
def test_csv_reconstructs_inferred_label_without_erasing_manual_override(priority, edited):
    final = ("dont_read" if priority != "dont_read" else "must_read") if edited else priority
    result = lp.provenance_from_row({
        "item_key": "PAPER001", "gold_signal_tier": "user_label", "matched_emojis": "👎",
        "in_trash": "True", "gold_priority_inferred": priority, "gold_priority_final": final,
        "gold_inferred_relevance": str(PRIORITY_TO_RELEVANCE[priority]),
    })
    assert result.derived_priority == priority
    assert result.derived_score == PRIORITY_TO_RELEVANCE[priority]
    assert result.persisted_priority == final
    assert result.is_manual_override is edited
    assert result.explicit_label == priority


@pytest.mark.parametrize("inferred", [None, "", "urgent", "label:must_read"])
def test_explicit_csv_row_requires_valid_original_label(inferred):
    with pytest.raises(ValueError, match="gold_priority_inferred"):
        lp.provenance_from_row({"item_key": "PAPER001", "gold_signal_tier": "user_label",
                                "gold_priority_inferred": inferred, "gold_priority_final": "must_read"})


def test_existing_tag_recognition_and_highest_label_rule_are_reused():
    result = lp.compute_provenance(item_key="PAPER001", title="Paper",
                                   tags=["label:could_read", "Label:Must_Read", "label:bogus", "👎"])
    assert result.derived_priority == "must_read"
    assert result.explicit_label == "must_read"
    unknown = lp.compute_provenance(item_key="PAPER001", title="Paper", tags=["label:bogus"])
    assert unknown.derived_priority == "could_read"
    assert unknown.explicit_label is None


def test_real_export_round_trip_to_http_provenance(tmp_path, monkeypatch):
    zdb = build_zotero_db(tmp_path / "zotero")
    item = add_library_item(zdb, item_key="PAPER001", title="Label only")
    add_tag_to_item(zdb, item_id=item, tag_name="label:must_read")
    csv_path = tmp_path / "golden.csv"
    goldenset.export_golden_dataset(zdb.parent, csv_path, tmp_path / "golden.jsonl")
    with csv_path.open() as stream:
        row = next(csv.DictReader(stream))
    assert row["gold_signal_tier"] == "user_label"
    assert row["matched_emojis"] == ""
    monkeypatch.setattr(_golden_helpers, "_golden_csv_path", lambda: csv_path)
    with TestClient(_app()) as client:
        response = client.get("/api/golden/provenance", params={"item_key": "PAPER001"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["derived_priority"] == "must_read"
    assert payload["short_circuits"]["explicit_label"] == "must_read"
    assert payload["is_manual_override"] is False
