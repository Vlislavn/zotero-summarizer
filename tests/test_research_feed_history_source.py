"""Owned SQLite source-history regressions; fixture relevance is not a human label."""
from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_research_feed import _review, _seed, _settings, deep_review
from zotero_summarizer.models import ResearchCandidate, ResearchProfile
from zotero_summarizer.services.research_feed import runner, source
from zotero_summarizer.storage import repositories


START = datetime(2026, 8, 20, tzinfo=timezone.utc)
END = datetime(2026, 8, 29, tzinfo=timezone.utc)
TOPICAL = "agent harness benchmark"
NEUTRAL = json.dumps({"summary": {
    "executive_summary": "Neutral observations", "methods": "Observations",
    "triage_confidence": 0.9,
}})


def _paper(settings, index, *, rss="", stored=TOPICAL, materialized=""):
    # _seed uses len(key) as feed_item_id: every index has a distinct length.
    key = "p" + "x" * index
    _seed(settings, key, f"Neutral record {index:02d}", materialized=materialized)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE rss_items SET abstract=? WHERE stable_feed_key=?", (rss, key))
        conn.execute(
            """UPDATE processed_feed_items SET abstract=?, decision='triaged_pending',
               composite_score=3, reading_priority='could_read', shap_contribs_json=?,
               model_version='fixture-model', created_at='2026-08-25T01:00:00Z',
               updated_at='2026-08-25T02:00:00Z' WHERE stable_feed_key=?""",
            (stored, NEUTRAL, key),
        )
    return key


def _source_rows(settings):
    with sqlite3.connect(settings.triage_db_path) as conn:
        return (
            conn.execute("SELECT * FROM rss_items ORDER BY id").fetchall(),
            conn.execute("SELECT * FROM processed_feed_items ORDER BY id").fetchall(),
        )


def _run(settings, **options):
    before = _source_rows(settings)
    if 'review_loader' not in options:
        with sqlite3.connect(settings.triage_db_path) as conn:
            keys = [row[0] for row in conn.execute('SELECT stable_feed_key FROM rss_items')]
        retained = {key: _review(key) for key in keys if isinstance(key, str) and key}
        options['review_loader'] = lambda key: deepcopy(retained[key])
    args = dict(start=START, end=END)
    args.update(options)
    result = runner.run_weekly(settings, **args)
    assert _source_rows(settings) == before
    return json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))


def _body(payload, key):
    entry = payload["metadata"]["source_history"][key]
    assert ("candidate" in entry) != ("candidate_ref" in entry)
    value = entry.get("candidate")
    if value is None:
        pointer = entry["candidate_ref"]
        assert pointer.startswith("/")
        value = payload
        for part in pointer[1:].split("/"):
            value = value[int(part)] if isinstance(value, list) else value[part]
    assert value["source_id"] == key
    return value


def _assert_one_home(payload):
    history = payload["metadata"]["source_history"]
    bodies = [record["candidate"] for record in payload["cards"]]
    bodies += [record["candidate"] for record in payload["rejected_near_threshold"]]
    bodies += payload["manual_full_text_required"]
    bodies += [row["candidate"] for row in payload.get("unqualified_reviews", [])]
    bodies += [entry["candidate"] for entry in history.values() if "candidate" in entry]
    for key in history:
        assert sum(body["source_id"] == key for body in bodies) == 1
        assert _body(payload, key) in bodies


def test_exact_recovery_precedes_triage_card_and_publication(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    raw = "\t\u2003\n"
    recovered = "\n  agent harness benchmark — Δ\u00a0 \n"
    key = _paper(settings, 1, rss=raw, stored=recovered)
    original = source.load_candidates(settings.triage_db_path, start=START, end=END, limit=10, venue="")[0]
    original_dump = original.model_dump(mode="json")
    triage_inputs, card_inputs = [], []
    triage, build = runner.triage_candidate, runner.build_card

    def inspect_triage(candidate, row, profile):
        triage_inputs.append(candidate.abstract)
        assert row["abstract"] == recovered
        return triage(candidate, row, profile)

    def inspect_card(candidate, *args, **kwargs):
        card_inputs.append(candidate.abstract)
        return build(candidate, *args, **kwargs)

    def forbidden(*_args, **_kwargs):
        pytest.fail("cached projection must not start review/model work")

    monkeypatch.setattr(runner, "triage_candidate", inspect_triage)
    monkeypatch.setattr(runner, "build_card", inspect_card)
    monkeypatch.setattr(runner, "_ensure_reviews", forbidden)
    monkeypatch.setattr(deep_review, "start", forbidden)
    payload = _run(settings, generate_reviews=False)
    assert payload["metadata"]["counts"]["cards_generated"] == 1
    assert triage_inputs == card_inputs == [recovered]
    assert _body(payload, key) == original.model_copy(update={"abstract": recovered}).model_dump(mode="json")
    assert _body(payload, key)["abstract"].encode() == recovered.encode()
    assert original.model_dump(mode="json") == original_dump
    entry = payload["metadata"]["source_history"][key]
    assert entry["original_rss_abstract"] == raw
    assert entry["text_basis"] == "stored" and entry["history"] == "accepted"
    assert entry["currentness"] == entry["content_version"] == "not_attested"
    assert entry["reason"] == "identity_compatible"
    assert type(entry["row_id"]) is int
    assert entry["run_id"] == "r1" and entry["model_version"] == "fixture-model"
    assert entry["created_at"] == "2026-08-25T01:00:00Z"
    assert entry["updated_at"] == "2026-08-25T02:00:00Z"
    assert payload["cards"][0]["provenance"]["basis"] == "full_text"
    assert entry["candidate_ref"] == "/cards/0/candidate"
    _assert_one_home(payload)


@pytest.mark.parametrize(("rss", "stored", "basis"), [
    (" \nagent harness benchmark Δ\t", None, "rss"),
    (" \nagent harness benchmark Δ\t", "", "rss"),
    (" \nagent harness benchmark Δ\t", " \nagent harness benchmark Δ\t", "rss"),
    ("\t\u2003\n", " \t", "not_available"),
    ("", None, "not_available"),
])
def test_rss_bytes_and_missing_both_are_not_normalized(tmp_path, rss, stored, basis):
    settings = _settings(tmp_path)
    key = _paper(settings, 1, rss=rss, stored=stored)
    payload = _run(settings)
    entry = payload["metadata"]["source_history"][key]
    assert entry["history"] == "accepted" and entry["text_basis"] == basis
    assert "original_rss_abstract" not in entry
    assert _body(payload, key)["abstract"] == rss
    assert payload["metadata"]["counts"]["triaged"] == 1
    _assert_one_home(payload)


@pytest.mark.parametrize("rss", ["\t\u2003\n", TOPICAL])
def test_missing_history_preserves_source_without_heuristic_cached_only_rescue(tmp_path, monkeypatch, rss):
    settings = _settings(tmp_path)
    key = _paper(settings, 1, rss=rss)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("DELETE FROM processed_feed_items WHERE stable_feed_key=?", (key,))
    candidate = source.load_candidates(settings.triage_db_path, start=START, end=END, limit=1, venue="")[0]
    profile = runner.load_profile(settings.data_dir)
    assert runner.triage_candidate(candidate, None, profile).include is False
    def forbidden(*args, **kwargs):
        pytest.fail("Missing qualified abstract cache cannot start clients or review work")
    from zotero_summarizer.services.llm import factory
    monkeypatch.setattr(factory, "resolve_stage_client", forbidden)
    monkeypatch.setattr(runner, "_ensure_reviews", forbidden)
    payload = _run(settings, queue_zotero=True, dry_run=False, review_loader=forbidden)
    entry = payload["metadata"]["source_history"][key]
    assert entry["history"] == "missing" and entry["reason"] == "no_prior_history"
    assert entry["currentness"] == entry["content_version"] == "not_attested"
    assert not {"row_id", "run_id", "model_version", "created_at", "updated_at"} & entry.keys()
    assert _body(payload, key) == candidate.model_dump(mode="json")
    assert entry["admission"] == {"status": "failed", "reason": "abstract_triage_unavailable", "native_decision": None}
    assert payload["rejected_near_threshold"] == [] and payload["cards"] == []
    assert payload["failed"] == [{"source_id": key, "error": "ValueError: cached abstract triage unavailable"}]
    assert payload["metadata"]["writebacks"]["attempted"] == 0
    _assert_one_home(payload)


def test_substantive_rss_and_blank_stored_text_preserve_the_rss_bytes(tmp_path):
    settings = _settings(tmp_path)
    rss = " \nagent harness benchmark Δ\t"
    key = _paper(settings, 1, rss=rss, stored=" \t")
    payload = _run(settings)
    entry = payload["metadata"]["source_history"][key]
    assert entry["history"] == "accepted" and entry["text_basis"] == "rss"
    assert "original_rss_abstract" not in entry
    assert _body(payload, key)["abstract"] == rss and len(payload["cards"]) == 1
    _assert_one_home(payload)


@pytest.mark.parametrize(("column", "value", "reason"), [
    ("source_type", "", "history_source_type_unverifiable"),
    ("source_type", "zotero", "history_source_type_unverifiable"),
    ("source_type", "rss", "history_source_type_unverifiable"),
    ("source_type", "APP_RSS", "history_source_type_unverifiable"),
    ("title", "neutral record 01", "history_title_mismatch"),
    ("title", "Neutral record 01 ", "history_title_mismatch"),
    ("title", "", "history_title_mismatch"),
    ("doi", "10.1/conflict", "history_doi_mismatch"),
    ("abstract", "agent harness benchmark", "history_abstract_conflict"),
    ("abstract", "PRIVATE incompatible text", "history_abstract_conflict"),
])
def test_incompatible_whole_row_is_withheld_and_peer_target_survives(tmp_path, column, value, reason):
    settings = _settings(tmp_path)
    rss = "  agent harness benchmark  "
    bad = _paper(settings, 1, rss=rss, stored=rss, materialized="BAD-TARGET")
    good = _paper(settings, 2, materialized="GOOD-TARGET")
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE rss_items SET doi='10.1/original' WHERE stable_feed_key=?", (bad,))
        conn.execute(
            """UPDATE processed_feed_items SET doi='10.1/original', decision='selected',
               composite_score=5, reading_priority='must_read' WHERE stable_feed_key=?""", (bad,),
        )
        # Column names are this test's fixed parameter list, never source input.
        conn.execute(f"UPDATE processed_feed_items SET {column}=? WHERE stable_feed_key=?", (value, bad))
    payload = _run(settings, queue_zotero=True, dry_run=False)
    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [good]
    assert payload["failed"] == [{"source_id": bad, "error": f"ValueError: {reason}"}]
    entry = payload["metadata"]["source_history"][bad]
    assert entry["history"] == "withheld" and entry["reason"] == reason
    assert entry["text_basis"] == "rss" and _body(payload, bad)["abstract"] == rss
    assert not {"row_id", "run_id", "model_version", "created_at", "updated_at"} & entry.keys()
    assert "BAD-TARGET" not in json.dumps(payload) and "PRIVATE" not in json.dumps(payload)
    with repositories.with_db_path(settings.triage_db_path):
        assert [change["item_key"] for change in repositories.get_pending_changes(status=None)] == ["GOOD-TARGET"]
    _assert_one_home(payload)


@pytest.mark.parametrize(("column", "value", "reason"), [
    ("stable_feed_key", None, "history_source_key_mismatch"),
    ("stable_feed_key", "other", "history_source_key_mismatch"),
    ("source_type", None, "history_source_type_unverifiable"),
    ("source_type", "", "history_source_type_unverifiable"),
    ("title", None, "history_title_mismatch"),
    ("title", "", "history_title_mismatch"),
    ("doi", 1, "history_doi_mismatch"),
    ("abstract", 1, "history_abstract_unverifiable"),
])
def test_admission_does_not_infer_missing_or_wrong_identity(column, value, reason):
    candidate = ResearchCandidate(source_id="key", source="app_rss", title="Neutral", abstract="\t")
    row = {"stable_feed_key": "key", "source_type": "app_rss", "title": "Neutral", "abstract": TOPICAL}
    row[column] = value
    before = dict(row)
    effective, entry = source.admit_history(candidate, row)
    assert effective is candidate and effective.abstract == "\t"
    assert entry["history"] == "withheld" and entry["reason"] == reason
    assert row == before
    if value is None:
        del row[column]
        assert source.admit_history(candidate, row)[1]["reason"] == reason


@pytest.mark.parametrize(("rss_doi", "stored_doi"), [
    (" https://doi.org/10.1/X/ ", "doi:10.1/x"),
    ("doi:10.1/x", "https://doi.org/10.1/X/"),
    ("10.1/x", "http://doi.org/10.1/X/"),
    ("https://dx.doi.org/10.1/X", "10.1/x"),
    (None, "10.1/x"), ("10.1/x", None),
])
def test_existing_doi_normalization_accepts_without_rewriting_originals(tmp_path, rss_doi, stored_doi):
    settings = _settings(tmp_path)
    key = _paper(settings, 1)
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE rss_items SET doi=? WHERE stable_feed_key=?", (rss_doi, key))
        conn.execute("UPDATE processed_feed_items SET doi=? WHERE stable_feed_key=?", (stored_doi, key))
    payload = _run(settings)
    assert payload["metadata"]["source_history"][key]["history"] == "accepted"
    assert _body(payload, key)["doi"] == rss_doi
    assert len(payload["cards"]) == 1


def test_newest_bad_row_never_rescues_older_selected_history_or_target(tmp_path):
    settings = _settings(tmp_path)
    bad = _paper(settings, 1, rss="\t", materialized="OLD-TARGET")
    good = _paper(settings, 2, materialized="GOOD-TARGET")
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET decision='selected' WHERE stable_feed_key=?", (bad,))
        conn.execute(
            """INSERT INTO processed_feed_items
               (feed_library_id, feed_item_id, source_type, stable_feed_key, guid, title,
                decision, composite_score, run_id, abstract, materialized_zotero_key)
               SELECT feed_library_id, feed_item_id+10000, 'zotero', stable_feed_key, guid, title,
                      'selected', 5, 'newest-bad', 'PRIVATE newest abstract', 'BAD-TARGET'
               FROM processed_feed_items WHERE stable_feed_key=?""", (bad,),
        )
    assert runner._latest_rows(settings.triage_db_path)[bad]["run_id"] == "newest-bad"
    payload = _run(settings, queue_zotero=True, dry_run=False)
    assert [record["candidate"]["source_id"] for record in payload["cards"]] == [good]
    assert _body(payload, bad)["abstract"] == "\t"
    assert payload["metadata"]["source_history"][bad]["history"] == "withheld"
    assert "newest-bad" not in json.dumps(payload) and "PRIVATE" not in json.dumps(payload)
    with repositories.with_db_path(settings.triage_db_path):
        assert [change["item_key"] for change in repositories.get_pending_changes(status=None)] == ["GOOD-TARGET"]


def test_all_source_admissions_have_one_home_across_every_output_cut(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    keys = {index: _paper(settings, index, stored=TOPICAL if index <= 8 else "Neutral observations")
            for index in range(1, 23)}
    with sqlite3.connect(settings.triage_db_path) as conn:
        conn.execute("UPDATE processed_feed_items SET shap_contribs_json='[]' WHERE stable_feed_key=?", (keys[21],))
        conn.execute("UPDATE processed_feed_items SET source_type='zotero' WHERE stable_feed_key=?", (keys[22],))
    admitted = source.load_candidates(settings.triage_db_path, start=START, end=END, limit=100, venue="")
    build = runner.build_card
    retained = {key: _review(key) for key in keys.values()}

    def loader(key):
        if key == keys[7]:
            return None
        if key == keys[6]:
            raise RuntimeError("fixture review failure")
        return deepcopy(retained[key])

    def build_one(candidate, *args, **kwargs):
        if candidate.source_id == keys[5]:
            raise ValueError("fixture card failure")
        return build(candidate, *args, **kwargs)

    monkeypatch.setattr(runner, "build_card", build_one)
    payload = _run(settings, shortlist_budget=5, card_budget=4, review_loader=loader)
    history = payload["metadata"]["source_history"]
    assert list(history) == [candidate.source_id for candidate in admitted]
    assert payload["metadata"]["counts"] == {
        "discovered": 22, "deduplicated": 22, "triaged": 20, "shortlisted": 5,
        "full_text_available": 2, "cards_generated": 1, "failed": 4,
    }
    assert history[keys[8]]["candidate_ref"] == "/cards/0/candidate"
    assert history[keys[7]]["candidate_ref"] == "/unqualified_reviews/0/candidate"
    assert payload["manual_full_text_required"] == []
    assert payload["unqualified_reviews"][0]["candidate"]["source_id"] == keys[7]
    assert payload["unqualified_reviews"][0]["reason"] == "review_missing"
    assert len(payload["rejected_near_threshold"]) == 10
    assert sum("candidate" in history[keys[index]] for index in range(9, 21)) == 2
    for index in [1, 2, 3, 4, 5, 6, 21, 22]:
        assert "candidate" in history[keys[index]]
    for candidate in admitted:
        index = next(index for index, key in keys.items() if key == candidate.source_id)
        abstract = candidate.abstract if index == 22 else TOPICAL if index <= 8 else "Neutral observations"
        assert _body(payload, candidate.source_id) == candidate.model_copy(update={"abstract": abstract}).model_dump(mode="json")
    assert history[keys[21]]["history"] == "accepted"  # Later triage failure does not erase admission.
    assert history[keys[22]]["history"] == "withheld"
    _assert_one_home(payload)
    capped = _run(settings, source_limit=4)
    assert list(capped["metadata"]["source_history"]) == [candidate.source_id for candidate in admitted[:4]]
    _assert_one_home(capped)


def test_metadata_exact_pointer_lists_immutability_and_finite_provenance():
    candidate = ResearchCandidate(
        source_id="id/Δ", source="app_rss", title="Neutral Δ", abstract="\t\u2003",
        authors=["李", "Δ", "李"], code_urls=["https://x.test/Δ", "https://x.test/a"],
        dataset_urls=["https://x.test/z", "https://x.test/z"], model_urls=["https://x.test/μ"],
    )
    before = candidate.model_dump(mode="json")
    row = {"stable_feed_key": candidate.source_id, "source_type": "app_rss", "title": candidate.title,
           "abstract": "\n agent harness Δ \t", "id": float("inf"), "run_id": float("nan"),
           "model_version": 7, "created_at": None, "updated_at": "literal timestamp",
           "composite_score": float("inf"), "shap_contribs_json": "PRIVATE"}
    effective, entry = source.admit_history(candidate, row)
    assert candidate.model_dump(mode="json") == before
    assert effective is not candidate and effective.authors == before["authors"]
    assert "row_id" not in entry and entry["run_id"] is None and entry["model_version"] is None
    assert entry["created_at"] is None and entry["updated_at"] == "literal timestamp"
    assert "composite_score" not in entry and "shap_contribs_json" not in entry
    body = effective.model_dump(mode="json")
    wrong = {**body, "code_urls": list(reversed(body["code_urls"]))}
    history = source.source_history_metadata([effective], {candidate.source_id: dict(entry)}, [{"candidate": wrong}], [], [])
    assert history[candidate.source_id]["candidate"] == body
    history = source.source_history_metadata([effective], {candidate.source_id: dict(entry)}, [{"candidate": body}], [], [])
    payload = {"metadata": {"source_history": history}, "cards": [{"candidate": body}]}
    assert history[candidate.source_id]["candidate_ref"] == "/cards/0/candidate"
    assert _body(payload, candidate.source_id) == body
    assert effective.model_dump(mode="json")["dataset_urls"] == before["dataset_urls"]
    json.dumps(history, allow_nan=False)


@pytest.mark.parametrize(("decision", "text", "score", "priority", "included", "ordinal"), [
    ("selected", "Neutral", 1, None, True, 1),
    ("user_rejected", TOPICAL, 5, None, False, 5),
    ("gate_rejected", TOPICAL, 5, None, False, 5),
    ("corpus_rejected", TOPICAL, 3, None, True, 3),
    ("triaged_pending", TOPICAL, 2.6, None, True, 3),
    ("triaged_pending", "Neutral", 5, "could_read", False, 3),
])
def test_pure_triage_decision_numeric_and_direct_fixture_api_are_preserved(decision, text, score, priority, included, ordinal):
    candidate = ResearchCandidate(source_id="direct", source="app_rss", title="Neutral", abstract=text)
    row = {"decision": decision, "composite_score": score, "reading_priority": priority,
           "shap_contribs_json": NEUTRAL}
    result = runner.triage_candidate(candidate, row, ResearchProfile(themes=["agent"], projects=["harness"]))
    assert result.include is included and result.score == ordinal and result.confidence == 0.9
    with pytest.raises(ValueError, match="^stored triage summary is invalid JSON$"):
        source.summary({"shap_contribs_json": "[]"})


def test_existing_selected_fixture_remains_usable(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings, "old-fixture", "Agent harness benchmark")
    payload = _run(settings)
    assert payload["metadata"]["counts"]["cards_generated"] == 1
    assert payload["cards"][0]["triage"]["score"] == 5
    _assert_one_home(payload)
