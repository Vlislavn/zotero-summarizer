"""Cursor corruption, retained-report admission and failure isolation controls."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from tests.test_research_feed import _review, _seed, _settings
from tests.test_research_feed_cursor import START, END, environment, run, stage_calls, state_path  # noqa: F401
from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.services.library._digest_audit import canonical_hash
from zotero_summarizer.services.research_feed import cursor, render, runner


def _abstract_cache(settings, monkeypatch):
    from types import SimpleNamespace
    from zotero_summarizer.runtime import get_context
    from zotero_summarizer.services._common import read_config
    from zotero_summarizer.services.research_feed import source
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    from zotero_summarizer.services.triage import summarization

    settings.config_path.write_text(_default_goals_config().model_dump_json())
    config = read_config(settings.config_path, settings.calibration_path)
    get_context().state.app_state = SimpleNamespace(config=config)
    monkeypatch.setattr(cursor.factory, 'resolve_stage_client', Mock(return_value=object()))
    generate = Mock(return_value=SummarizeResponse(
        executive_summary='Owned abstract summary', relevance_score=4,
        triage_rationale='Declared fixture relevance', composite_relevance_score=4,
        reading_priority='should_read'))
    monkeypatch.setattr(summarization, 'run_abstract_dry', generate)
    candidate = source.load_candidates(settings.triage_db_path, start=START, end=END, limit=1, venue='')[0]
    profile = runner.load_profile(settings.data_dir)
    checkpoint = cursor.CandidateCursor(settings)
    row = checkpoint.row(candidate, profile)
    return checkpoint, candidate, profile, row, generate


def _one(environment):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    payload, result = run(settings)
    return settings, reviews, payload, result


@pytest.mark.parametrize("owner", ["state_file", "report_root"])
def test_symlinked_weekly_owner_refuses_before_source_or_model_admission(tmp_path, monkeypatch, owner):
    settings = _settings(tmp_path)
    _seed(settings, "native-owned", "Agent evaluation")
    root = settings.data_dir / "research_feed"
    outside = settings.data_dir / "not-weekly-owned"
    outside.mkdir()
    sentinel = outside / "sentinel.json"
    sentinel.write_bytes(b'{"owned":"unchanged"}')
    if owner == "report_root":
        root.symlink_to(outside, target_is_directory=True)
        link = root
    else:
        root.mkdir()
        link = root / "state.json"
        link.symlink_to(sentinel)
    forbidden = Mock(side_effect=AssertionError("symlink admission must precede source/model work"))
    monkeypatch.setattr(runner._feed_assessment, "cached_reader", forbidden)
    monkeypatch.setattr(runner, "load_candidates", forbidden)
    monkeypatch.setattr(runner, "rank_candidates", forbidden)
    from tests.test_research_feed_boundaries import START, END

    with pytest.raises(ValueError, match="symlink"):
        runner.run_weekly(settings, start=START, end=END)
    forbidden.assert_not_called()
    assert link.is_symlink() and set(outside.iterdir()) == {sentinel}
    assert sentinel.read_bytes() == b'{"owned":"unchanged"}'


def test_shared_publisher_refuses_symlink_root_without_publishing_into_target(tmp_path):
    outside, root = tmp_path / "not-publisher-owned", tmp_path / "linked-output"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"preserve owned outside bytes")
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        render.persist({"owned": "synthetic news"}, root, "owned-bundle", render_markdown=lambda _payload: "Owned news")
    assert root.is_symlink() and set(outside.iterdir()) == {sentinel}
    assert sentinel.read_bytes() == b"preserve owned outside bytes"


@pytest.mark.parametrize("change", ["json", "envelope", "version", "state_version", "entry", "triage", "typed", "numeric_string", "checksum", "path"])
def test_present_malformed_cursor_is_loud_not_an_empty_success(environment, monkeypatch, change):
    settings, _reviews, _payload, _result = _one(environment)
    checkpoint, candidate, profile, _row, generate = _abstract_cache(settings, monkeypatch)
    path = checkpoint.path
    stored = json.loads(path.read_text())
    entry = stored[candidate.source_id]
    if change == 'json':
        path.write_text('not JSON')
    else:
        if change == 'envelope':
            stored = []
        elif change == 'version':
            entry['input_sha256'] = True
        elif change == 'state_version':
            entry['input_sha256'] = 999
        elif change == 'entry':
            stored[candidate.source_id] = []
        elif change in {'triage', 'typed', 'numeric_string'}:
            entry['summary']['relevance_score'] = '4' if change == 'numeric_string' else True
            if change in {'typed', 'numeric_string'}:
                entry['payload_sha256'] = canonical_hash({k: v for k, v in entry.items() if k != 'payload_sha256'})
        elif change == 'checksum':
            entry['summary']['executive_summary'] = 'Changed without checksum'
        else:
            entry['input_sha256'] = '../outside'
        path.write_text(json.dumps(stored))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        cursor.CandidateCursor(settings).row(candidate, profile, generate=False)
    assert path.read_bytes() == before and generate.call_count == 1


@pytest.mark.parametrize("link", ["state_file", "report_root"])
def test_cursor_refuses_symlinked_state_owners_before_processing(environment, link):
    settings, _reviews, _payload, _result = _one(environment)
    path = state_path(settings)
    before = path.read_bytes()
    if link == "state_file":
        outside = settings.data_dir / "unrelated-state.json"
        outside.write_bytes(before)
        path.unlink()
        path.symlink_to(outside)
    else:
        root = path.parent
        outside = settings.data_dir / "retained-original-root"
        root.rename(outside)
        root.symlink_to(outside, target_is_directory=True)
    with stage_calls() as calls, pytest.raises(ValueError):
        run(settings)
    assert calls == {"triage": 0, "card": 0, "rank": 0}
    assert path.read_bytes() == before


@pytest.mark.parametrize('damage', ['missing', 'symlink', 'json', 'group', 'checksum', 'candidate'])
def test_old_report_never_replays_or_repairs_current_projection(environment, damage):
    settings, _reviews, original, result = _one(environment)
    path = Path(result["json_path"])
    payload = deepcopy(original)
    if damage == "missing":
        path.unlink()
    elif damage == "symlink":
        outside = settings.data_dir / "unrelated.json"
        outside.write_text('{"not":"a weekly report"}')
        path.unlink()
        path.symlink_to(outside)
    elif damage == "json":
        path.write_text("not JSON")
    else:
        if damage == "group":
            payload["research_ideas_by_project"] = {"agent harness": {}}
        elif damage == "candidate":
            payload["cards"][0]["candidate"]["source_id"] = "other-candidate"
        else:
            payload["cards"][0]["card"]["problem"] = "changed without cursor checksum"
        path.write_text(json.dumps(payload))
    before = path.read_bytes() if path.exists() else None
    with stage_calls() as calls:
        rebuilt, _ = run(settings)
    assert calls == {"triage": 1, "card": 1, "rank": 1}
    assert 'candidate_cursor' not in rebuilt['metadata']
    assert rebuilt["cards"] == original["cards"]
    assert (path.read_bytes() if path.exists() else None) == before


def test_fresh_review_currentness_rejection_never_replays_old_card(environment):
    settings, reviews, _payload, _result = _one(environment)
    reviews["paper"]["current"] = False
    with stage_calls() as calls:
        missing, _ = run(settings)
    assert calls == {"triage": 1, "card": 0, "rank": 1}
    assert missing["cards"] == []
    assert missing["manual_full_text_required"] == []
    assert missing["unqualified_reviews"][0]["candidate"]["source_id"] == "paper"
    assert missing["unqualified_reviews"][0]["reason"] == "review_missing"
    assert 'candidate_cursor' not in missing['metadata']


@pytest.mark.parametrize("failure", ["source", "native", "ranking"])
def test_fresh_source_native_and_numeric_guards_still_run_on_previously_completed_paper(environment, failure):
    settings, _reviews, _payload, _result = _one(environment)
    with sqlite3.connect(settings.triage_db_path) as connection:
        if failure == "source":
            connection.execute("UPDATE rss_items SET title='incompatible title'")
        elif failure == "native":
            connection.execute("UPDATE processed_feed_items SET decision='skipped_error'")
        else:
            connection.execute("UPDATE processed_feed_items SET shap_contribs_json=?", (json.dumps({
                "summary": {}, "aux_context": {"goal_sims": {"valid": 0.8, "bad": None}},
            }),))
    payload, _ = run(settings)
    assert payload["cards"] == [] and len(payload["failed"]) == 1
    assert 'candidate_cursor' not in payload['metadata']
    assert payload["metadata"]["ranking"]["inputs"]["paper"]["status"] == (
        "failed" if failure == "ranking" else "not_applicable"
    )
    repeated, _ = run(settings)
    assert repeated["cards"] == [] and len(repeated["failed"]) == 1
    assert 'candidate_cursor' not in repeated['metadata']


def test_atomic_state_failure_retains_old_successful_cursor_and_retries(environment, monkeypatch):
    settings, _reviews, _payload, result = _one(environment)
    before = state_path(settings).read_bytes()
    old_report = Path(result["json_path"]).read_bytes()
    with sqlite3.connect(settings.triage_db_path) as connection:
        connection.execute("UPDATE processed_feed_items SET composite_score=3.25")
    write = runner.write_json_atomic
    monkeypatch.setattr(runner, "write_json_atomic", Mock(side_effect=OSError("synthetic state failure")))
    with pytest.raises(OSError):
        run(settings)
    assert state_path(settings).read_bytes() == before
    assert Path(result["json_path"]).read_bytes() == old_report
    monkeypatch.setattr(runner, "write_json_atomic", write)
    with stage_calls() as calls:
        payload, _ = run(settings)
    assert calls == {"triage": 1, "card": 1, "rank": 1}
    assert payload["metadata"]["ranking"]["inputs"]["paper"]["relevance"] == 3.25


def test_legacy_window_pointer_is_cold_and_upgrades_only_after_publication(environment):
    settings, reviews = environment
    _seed(settings, "paper", "Agent harness benchmark")
    reviews["paper"] = _review("paper")
    path = state_path(settings)
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":1,"last_to":"legacy-window"}')
    with stage_calls() as first_calls:
        first, _ = run(settings)
    assert first_calls == {"triage": 1, "card": 1, "rank": 1}
    assert 'candidate_cursor' not in first['metadata']
    published = json.loads(path.read_text())
    assert published['schema_version'] == 1 and 'candidate_cursor' not in published
    assert json.loads(Path(published['last_json']).read_text()) == first


def test_stale_stage_schema_output_is_not_reused_or_required_to_match_current_dto(environment, monkeypatch):
    settings, _reviews, _payload, _result = _one(environment)
    checkpoint, candidate, profile, original, generate = _abstract_cache(settings, monkeypatch)
    stored = json.loads(checkpoint.path.read_text())
    entry = stored[candidate.source_id]
    entry['input_sha256'] = '0' * 64
    del entry['summary']['executive_summary']
    entry['payload_sha256'] = canonical_hash({k: v for k, v in entry.items() if k != 'payload_sha256'})
    checkpoint.path.write_text(json.dumps(stored))
    before = checkpoint.path.read_bytes()
    with pytest.raises(ValueError, match='identity stale'):
        cursor.CandidateCursor(settings).row(candidate, profile, generate=False)
    assert checkpoint.path.read_bytes() == before and generate.call_count == 1
    assert cursor.CandidateCursor(settings).row(candidate, profile) == original
    assert generate.call_count == 2
    assert json.loads(checkpoint.path.read_text())[candidate.source_id]['input_sha256'] != '0' * 64


def test_retention_uses_existing_source_ceiling_and_keeps_latest_successful_versions(environment, monkeypatch):
    settings, _reviews, _payload, _result = _one(environment)
    original, candidate, profile, row, generate = _abstract_cache(settings, monkeypatch)
    bound = 5000
    entry = original.entries[candidate.source_id]
    padding = {f'synthetic:{index}': entry for index in range(bound - 1)}
    original.path.write_text(json.dumps({candidate.source_id: entry, **padding}))
    checkpoint = cursor.CandidateCursor(settings)
    with pytest.raises(ValueError, match='identity stale'):
        checkpoint.row(candidate.model_copy(update={'source_id': 'synthetic:0'}), profile, generate=False)
    checkpoint.row(candidate.model_copy(update={'source_id': 'next'}), profile)
    assert len(checkpoint.entries) == bound and candidate.source_id not in checkpoint.entries
    checkpoint.row(candidate.model_copy(update={'source_id': 'synthetic:0'}), profile)
    checkpoint.row(candidate.model_copy(update={'source_id': 'next-again'}), profile)
    assert len(checkpoint.entries) == bound
    assert 'synthetic:0' in checkpoint.entries and 'synthetic:1' not in checkpoint.entries
    before = checkpoint.path.read_bytes()
    assert original.row(candidate, profile, generate=False) == row
    assert checkpoint.path.read_bytes() == before and generate.call_count == 4
