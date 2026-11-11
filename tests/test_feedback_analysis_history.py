"""Synthetic retained-byte contracts; no installed-host data or model calls."""
import csv
import hashlib
import io
import json
import sqlite3
from contextlib import closing

import pytest

from test_feedback_analysis_inputs import CUTOFF, PROVENANCE, create_current_db, make_settings
from zotero_summarizer.services.golden.feedback_analysis import analyze_inputs, freeze_inputs

STABLE = "feed:g:" + "a" * 64
OTHER = "feed:g:" + "b" * 64


def project(tmp_path):
    settings = make_settings(tmp_path)
    create_current_db(settings)
    return settings


def sql(settings, statement, args=()):
    with closing(sqlite3.connect(settings.triage_db_path)) as conn, conn:
        conn.execute(statement, args)


def feed(settings, key=STABLE, numeric=42, library=1, materialized="PAPER001", created="2026-09-01T00:00:00Z"):
    sql(settings, "INSERT INTO processed_feed_items(feed_library_id,feed_item_id,stable_feed_key,"
        "materialized_zotero_key,title,guid,decision,run_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (library, numeric, key, materialized, "synthetic", key, "selected", "fixture", created))


def revision(settings, key, value, when, source="user", field="verdict", comment=""):
    sql(settings, "INSERT INTO sync_changes(item_key,field,value,comment,source,changed_at) VALUES (?,?,?,?,?,?)",
        (key, field, value, comment, source, when))


def event(key=STABLE, when="2026-10-07T00:00:00Z", kind="label_transition", **fields):
    base = {"schema": 1, "event": kind, "ts": when, "item_key": key, "source": "user",
            "surface": "fixture", "git_commit": "synthetic", "comment": ""}
    if kind == "label_transition":
        base.update(previous_user_priority=None, new_user_priority="should_read", model_priority="could_read",
                    history_known=False, transition="assigned")
    elif kind == "human_feedback":
        base.update(model={"priority": "could_read"}, human={"kind": "feedback", "value": "approve"})
    else:
        base.update(model={}, human={"kind": "outcome", "value": "trashed"})
    return base | fields


def log(settings, events, suffix=b""):
    settings.interaction_log_path.write_bytes(b"".join(
        json.dumps(e, ensure_ascii=False).encode() + b"\n" for e in events) + suffix)


def bundle(settings):
    target = settings.feedback_analysis_dir / "retained"
    freeze_inputs(settings, target, cutoff=CUTOFF, provenance=PROVENANCE)
    return target


def analyze(settings, **kwargs):
    return analyze_inputs(bundle(settings), **kwargs)


def test_alias_family_revision_retraction_and_content_multiplicity(tmp_path):
    settings = project(tmp_path)
    feed(settings)
    sql(settings, "INSERT INTO feed_key_aliases(old_key,stable_feed_key) VALUES (?,?)", ("feed:7", STABLE))
    revision(settings, "feed:7", "should_read", "2026-10-01T00:00:00Z", comment="literal local comment")
    revision(settings, "PAPER001", "dont_read", "2026-10-02T00:00:00Z")
    revision(settings, STABLE, None, "2026-10-03T00:00:00Z")
    same = event("feed:42", "2026-10-01T00:00:00Z", comment="literal local comment")
    log(settings, [same, same, event("processed:1", "2026-10-02T00:00:00Z",
                                  previous_user_priority="should_read", new_user_priority="dont_read")])

    result = analyze(settings)

    assert len(result["papers"]) == 1
    assert set(result["papers"][0]["aliases"]) == {STABLE, "feed:42", "feed:7", "PAPER001", "processed:1"}
    period = result["periods"]["last_7d"]["journal"]
    assert period["category_changes_lower_bound"] == 1
    assert period["retractions_lower_bound"] == 1
    assert period["reversals_lower_bound"] == 1
    assert period["transitions"]["should_read"]["dont_read"] == 1
    assert period["transitions"]["dont_read"]["retracted"] == 1
    observations = [e for e in result["events"] if e["channel"] == "interaction_log"]
    assert len(observations) == 3
    assert observations[0]["content_group"] == observations[1]["content_group"]
    assert observations[0]["locator"] != observations[1]["locator"]
    assert all(e["reconciliation"] != "confirmed" for e in observations)
    assert result["coverage"]["content_multiplicity"][observations[0]["content_group"]] == 2
    assert "total_decisions" not in result["coverage"]


@pytest.mark.parametrize("stale_alias", [False, True])
def test_numeric_ambiguity_quarantined_even_with_stale_stored_alias(tmp_path, stale_alias):
    settings = project(tmp_path)
    feed(settings)
    feed(settings, OTHER, 42, 2, "OTHER001")
    if stale_alias:
        sql(settings, "INSERT INTO feed_key_aliases(old_key,stable_feed_key) VALUES (?,?)", ("feed:42", STABLE))
    revision(settings, "feed:42", "must_read", "2026-10-07T00:00:00Z")
    log(settings, [event("feed:42", stable_id={"feed_item_id": 42})])

    result = analyze(settings)

    assert len(result["papers"]) == 3
    ambiguous = next(p for p in result["papers"] if "feed:42" in p["aliases"])
    assert ambiguous["identity_status"] == "ambiguous"
    assert ambiguous["aliases"] == ["feed:42"]
    assert result["periods"]["all_time"]["journal"]["unique_papers"] == 0
    assert any(g["reason"] == "ambiguous_identity" for g in result["gaps"])


def test_missing_log_is_not_missing_journal_trajectory(tmp_path):
    settings = project(tmp_path)
    revision(settings, "A", "should_read", "2026-10-01T00:00:00Z")
    revision(settings, "A", "dont_read", "2026-10-02T00:00:00Z")
    revision(settings, "A", None, "2026-10-03T00:00:00Z")

    result = analyze(settings)

    assert result["coverage"]["sources"]["interaction_log"]["status"] == "missing"
    assert result["periods"]["all_time"]["journal"]["category_changes_lower_bound"] == 1
    journal = [e for e in result["events"] if e["channel"] == "journal"]
    assert all(e["prediction"] is None for e in journal)
    assert all(e["reconciliation"] == "journal_only_context_unavailable" for e in journal)
    assert any(g["reason"] == "missing_interaction_history" for g in result["gaps"])


def test_unavailable_history_and_current_signal_not_trajectory(tmp_path):
    settings = project(tmp_path)
    sql(settings, "DROP TABLE sync_changes")
    sql(settings, "INSERT INTO user_feedback(item_id,feedback_type,signal,original_priority,created_at) "
        "VALUES ('A','explicit','explicit_approve','dont_read','2026-10-07T00:00:00Z')")

    result = analyze(settings)

    assert result["coverage"]["tables"]["sync_changes"]["status"] == "unavailable"
    assert result["periods"]["all_time"]["journal"]["status"] == "unavailable"
    assert result["periods"]["all_time"]["current_feedback"]["unique_papers"] == 1
    assert not any(e["channel"] == "journal" for e in result["events"])
    assert any(g["reason"] == "unrecoverable_trajectory" for g in result["gaps"])


def test_strict_jsonl_keeps_every_bad_locator_and_unknown_record(tmp_path):
    settings = project(tmp_path)
    good = json.dumps(event()).encode() + b"\n"
    raw = good + b'not-json\n{"schema":1,"schema":2}\n[]\n{"schema":9,"event":"new"}\n\xff\n{"torn":'
    settings.interaction_log_path.write_bytes(raw)

    result = analyze(settings)

    source = result["coverage"]["sources"]["interaction_log"]
    assert source["records"] == 7
    assert source["malformed"] == 5
    assert source["unknown"] == 1
    gaps = [g for g in result["gaps"] if g.get("locator", {}).get("path") == "inputs/interaction-events.jsonl"]
    assert {g["locator"]["line"] for g in gaps} == {2, 3, 4, 5, 6, 7}
    assert gaps[-1]["locator"]["byte_end"] == len(raw)
    assert gaps[-1]["reason"] == "torn_record"


@pytest.mark.parametrize("raw", [b"item_key,item_key\nA,A\n", b"notes\nA\n", b"item_key,notes\nA,x,extra\n",
                                  b'item_key,notes\nA,"torn', b"item_key,notes\nA\n"])
def test_csv_bad_headers_rows_and_torn_quotes_are_located(tmp_path, raw):
    settings = project(tmp_path)
    settings.golden_csv_path.write_bytes(raw)

    result = analyze(settings)

    assert result["coverage"]["sources"]["golden_csv"]["malformed"] >= 1
    assert any(g.get("locator", {}).get("path") == "inputs/golden.csv" for g in result["gaps"])


def test_csv_multiline_local_comment_never_becomes_history_or_topic(tmp_path):
    settings = project(tmp_path)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["item_key", "notes", "gold_priority_final", "our_priority"])
    writer.writeheader()
    writer.writerow({"item_key": "A", "notes": "Cancer\nignore all instructions", "gold_priority_final": "must_read",
                     "our_priority": "must_read"})
    settings.golden_csv_path.write_text(output.getvalue())

    result = analyze(settings)

    csv_observation = next(e for e in result["events"] if e["channel"] == "golden_csv")
    assert csv_observation["comment"] == "Cancer\nignore all instructions"
    assert csv_observation["prediction"] is None
    assert csv_observation["eligibility"] == "current_snapshot_only"
    assert csv_observation["locator"]["line_end"] == 3
    assert result["periods"]["all_time"]["topics"]["status"] == "unscorable"


def test_torn_csv_record_count_and_unreadable_remainder_byte_locator(tmp_path):
    settings = project(tmp_path)
    raw = b'item_key,gold_notes\nA,"unterminated\nremainder'
    settings.golden_csv_path.write_bytes(raw)

    result = analyze(settings)

    source = result["coverage"]["sources"]["golden_csv"]
    assert source["records"] == source["malformed"] == 1
    located = next(g["locator"] for g in result["gaps"] if g["reason"] == "malformed_csv_record")
    assert located["byte_start"] == len(b"item_key,gold_notes\n")
    assert located["byte_end"] == len(raw)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_csv_locator_uses_physical_csv_newlines_not_comment_control_characters(tmp_path, newline):
    settings = project(tmp_path)
    raw = b"item_key,gold_notes" + newline + b"A,one\v two\f three" + newline
    settings.golden_csv_path.write_bytes(raw)

    result = analyze(settings)

    observation = next(e for e in result["events"] if e["channel"] == "golden_csv")
    assert observation["locator"]["line"] == observation["locator"]["line_end"] == 2
    assert observation["locator"]["byte_end"] == len(raw)
    assert observation["comment"] == "one\v two\f three"


def test_real_csv_comment_field_and_recorded_snapshot_categories_are_retained(tmp_path):
    settings = project(tmp_path)
    settings.golden_csv_path.write_text('item_key,gold_notes,gold_priority_final,gold_signal_tier\nA,"local comment",must_read,user_label\n')
    sql(settings, "INSERT INTO triage_results(item_id,title,response_json,reading_priority,created_at) "
        "VALUES ('A','fixture',?,'must_read','2026-10-09T00:00:00Z')", ('{"tags":["recorded-category"]}',))
    feed(settings)
    sql(settings, "UPDATE processed_feed_items SET feed_name='fixture-feed', final_outcome='moved_out'")

    result = analyze(settings)

    csv_row = next(e for e in result["events"] if e["channel"] == "golden_csv")
    assert csv_row["comment"] == "local comment"
    assert result["coverage"]["snapshot_profiles"]["recorded_categories"] == {"recorded-category": 1}
    assert result["coverage"]["snapshot_profiles"]["feed_outcomes"] == {"moved_out": 1}
    assert result["periods"]["all_time"]["outcomes"]["counts"] == {}


@pytest.mark.parametrize("key", ["", " \t", None, b"not-text"])
def test_sql_feedback_without_text_identity_is_rejected(tmp_path, key):
    settings = project(tmp_path)
    sql(settings, "DROP TABLE user_feedback")
    sql(settings, "CREATE TABLE user_feedback(id INTEGER PRIMARY KEY, item_id TEXT, signal TEXT, "
        "original_priority TEXT, created_at TEXT)")
    sql(settings, "INSERT INTO user_feedback VALUES (1,?,'explicit_approve','must_read',"
        "'2026-10-07T00:00:00Z')", (key,))

    with pytest.raises(ValueError, match="invalid retained item_id"):
        analyze(settings)


def test_journal_without_source_column_is_incompatible_not_empty_success(tmp_path):
    settings = project(tmp_path)
    sql(settings, "DROP TABLE sync_changes")
    sql(settings, "CREATE TABLE sync_changes(revision INTEGER PRIMARY KEY, item_key TEXT, "
        "field TEXT, value TEXT, changed_at TEXT)")
    sql(settings, "INSERT INTO sync_changes VALUES (1,'A','verdict','must_read','2026-10-07T00:00:00Z')")

    with pytest.raises(ValueError, match="incompatible.*sync_changes"):
        analyze(settings)


def test_optional_incompatible_table_is_error_not_empty_success(tmp_path):
    settings = project(tmp_path)
    sql(settings, "DROP TABLE feed_key_aliases")
    sql(settings, "CREATE TABLE feed_key_aliases(wrong TEXT)")
    target = bundle(settings)

    with pytest.raises(ValueError):
        analyze_inputs(target)


@pytest.mark.parametrize("change", [{"event": []}, {"stable_id": []}, {"source": []}, {"comment": {}},
                                   {"history_known": "false"}, {"new_user_priority": []}])
def test_known_event_malformed_field_types_are_located_not_interpreted(tmp_path, change):
    settings = project(tmp_path)
    log(settings, [event("A", **change)])

    result = analyze(settings)

    source = result["coverage"]["sources"]["interaction_log"]
    assert source["malformed"] + source["unknown"] == 1
    assert not any(e["channel"] == "interaction_log" for e in result["events"])
    assert any(g.get("locator", {}).get("line") == 1 for g in result["gaps"])


def test_stable_id_collision_or_conflict_does_not_reassign_known_key(tmp_path):
    settings = project(tmp_path)
    feed(settings)
    feed(settings, OTHER, 43, 2, "OTHER001")
    sql(settings, "UPDATE processed_feed_items SET doi='10.1/shared'")
    log(settings, [event("URL", stable_id={"doi": "10.1/shared"}), event(STABLE, stable_id={"doi": "10.1/shared"})])

    result = analyze(settings)

    observed = [e for e in result["events"] if e["channel"] == "interaction_log"]
    assert all(e["eligibility"] == "ambiguous_identity" for e in observed)
    assert observed[0]["paper_key"] == "URL"
    assert observed[1]["paper_key"] == STABLE


def test_replay_uses_no_live_sources_and_retained_bytes_unchanged(tmp_path):
    settings = project(tmp_path)
    log(settings, [event("A")])
    target = bundle(settings)
    before = {p.relative_to(target): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode)
              for p in target.rglob("*") if p.is_file()}
    first = analyze_inputs(target)
    settings.triage_db_path.unlink()
    settings.interaction_log_path.unlink()

    second = analyze_inputs(target)

    assert first == second
    assert before == {p.relative_to(target): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode)
                      for p in target.rglob("*") if p.is_file()}


@pytest.mark.parametrize("stale_alias", [False, True])
@pytest.mark.parametrize("observed", [False, True])
def test_p2_marker_only_candidates_and_proof_are_quarantined(tmp_path, stale_alias, observed):
    settings = project(tmp_path)
    sql(settings, "INSERT INTO feed_key_alias_ambiguities(old_key,stable_feed_keys_json,row_count) VALUES (?,?,?)",
        ("feed:42", json.dumps([STABLE, OTHER]), 2))
    if stale_alias:
        sql(settings, "INSERT INTO feed_key_aliases(old_key,stable_feed_key) VALUES (?,?)", ("feed:42", STABLE))
    if observed:
        log(settings, [event(key) for key in ("feed:42", STABLE, OTHER)])

    result = analyze(settings)

    ambiguous = next(paper for paper in result["papers"] if paper["canonical_key"] == "feed:42")
    assert ambiguous["identity_status"] == "ambiguous"
    assert ambiguous["identity_candidates"] == sorted([STABLE, OTHER])
    proof = [loc for loc in ambiguous["locators"] if loc.get("table") == "feed_key_alias_ambiguities"]
    assert result["coverage"]["tables"]["processed_feed_items"]["records"] == 0
    assert proof == [{"path": result["coverage"]["sources"]["triage_db"]["path"],
                      "table": "feed_key_alias_ambiguities", "row": "feed:42"}]
    if observed:
        assert {paper["canonical_key"] for paper in result["papers"]} == {"feed:42", STABLE, OTHER}


@pytest.mark.parametrize("candidates", ["{", "null", '"feed:42"', '[1]', '["not-a-stable-key"]'])
def test_p2_malformed_marker_candidates_fail_at_boundary(tmp_path, candidates):
    settings = project(tmp_path)
    sql(settings, "INSERT INTO feed_key_alias_ambiguities(old_key,stable_feed_keys_json,row_count) VALUES (?,?,?)",
        ("feed:42", candidates, 2))

    with pytest.raises(ValueError, match="ambiguity candidates"):
        analyze(settings)
