"""Outside-in eligibility, version, bounds, and read-only result contract."""
import hashlib
import inspect
import json
from datetime import timedelta

import pytest

from test_feedback_analysis_history import STABLE, analyze, bundle, event, feed, log, project, revision, sql
from test_feedback_analysis_inputs import CUTOFF
from test_feedback_analysis_metrics import feedback
from zotero_summarizer.services.golden.feedback_analysis import AnalysisBudgets, BudgetExceeded, analyze_inputs
from zotero_summarizer.services.golden.feedback_analysis.provenance import canonical_bytes


def test_exact_window_boundaries_cutoff_and_unknown_times(tmp_path):
    settings = project(tmp_path)
    times = [CUTOFF - timedelta(days=d) for d in (0, 7, 8, 30, 31)]
    for i, dt in enumerate(times):
        feedback(settings, str(i), "approve", "must_read", dt.isoformat())
    for i, when in enumerate(["2026-10-08T00:00:00.000001Z", "2026-10-07", "2026-10-07 00:00:00", None]):
        feedback(settings, "excluded" + str(i), "reject", "dont_read", when)

    result = analyze(settings)

    assert [result["periods"][w]["current_feedback"]["unique_papers"]
            for w in ("last_7d", "last_30d", "all_time")] == [2, 4, 5]
    excluded = [e for e in result["events"] if e["item_key"].startswith("excluded")]
    assert [e["eligibility"] for e in excluded] == ["after_cutoff", "unknown_time", "unknown_time", "unknown_time"]
    assert excluded[2]["time_basis"] == "naive_origin_unproven"


def test_future_labels_comments_and_items_never_enter_eligibility(tmp_path):
    settings = project(tmp_path)
    feed(settings, created="2026-10-09T00:00:00Z")
    log(settings, [event(STABLE), event("A", "2026-10-09T00:00:00Z", comment="future secret"),
                   event("B", "2026-09-01T00:00:00Z"), event("B", "2026-10-07T00:00:00Z", comment="later")])
    revision(settings, "A", "must_read", "2026-10-09T00:00:00Z", comment="future rationale")
    revision(settings, "B", "local later note", "2026-10-09T00:00:00Z", field="review_note")

    result = analyze(settings)

    chronology = result["chronological"]
    assert chronology["status"] == "eligibility_only"
    assert chronology["split_at"] == "2026-09-08T00:00:00Z"
    assert chronology["earlier_papers"] == ["B"]
    assert "B" not in chronology["later_papers"]
    assert STABLE not in chronology["later_papers"]
    usable = set(chronology["eligible_observation_locators"])
    for e in result["events"]:
        if e["item_key"] == "A" or (e["item_key"] == "B" and e["kind"] == "review_note"):
            assert e["observation_id"] not in usable
    assert chronology["scored_evaluation"] is None
    assert any(g["reason"] == "future_item" for g in result["gaps"])


def test_configured_split_is_recorded_group_disjoint_and_replayable(tmp_path):
    settings = project(tmp_path)
    feed(settings)
    log(settings, [event("feed:42", "2026-09-01T00:00:00Z"), event("PAPER001"), event("A")])
    target = bundle(settings)

    result = analyze_inputs(target, budgets={"split_at": "2026-10-01T00:00:00Z"})

    chronology = result["chronological"]
    assert chronology["split_at"] == "2026-10-01T00:00:00Z"
    assert set(chronology["earlier_papers"]).isdisjoint(chronology["later_papers"])
    # Aliased identities lack historic mapping proof, so no feature/exposure claim is made.
    assert chronology["alias_history"] == "current_mapping_only"
    assert chronology["historical_features"] == "unavailable"
    assert result == analyze_inputs(target, budgets={"split_at": "2026-10-01T00:00:00Z"})


@pytest.mark.parametrize("value", [{"max_records": 0}, {"max_records": True}, {"unknown": 1},
                                  {"max_source_bytes": 256 * 1024 * 1024 + 1},
                                  {"max_output_bytes": 64 * 1024 * 1024 + 1},
                                  {"split_at": "2026-01-01"}, {"wall_cap_seconds": float("nan")}, []])
def test_invalid_configuration_before_bundle_io(tmp_path, value):
    with pytest.raises(ValueError):
        analyze_inputs(tmp_path / "absent", budgets=value)


@pytest.mark.parametrize("raw", ["bad", "nan", "inf", "0", "601"])
def test_bad_environment_not_ignored_by_explicit_override(tmp_path, monkeypatch, raw):
    monkeypatch.setenv("ZS_FEEDBACK_ANALYSIS_WALL_CAP_SECONDS", raw)
    with pytest.raises(ValueError):
        analyze_inputs(tmp_path / "absent", budgets={"wall_cap_seconds": 10})


@pytest.mark.parametrize("bound", ["max_records", "max_source_bytes", "max_line_bytes", "max_decoded_bytes", "max_output_bytes"])
def test_work_and_output_bounds_do_not_mutate_bundle(tmp_path, bound):
    settings = project(tmp_path)
    log(settings, [event("A"), event("B")])
    target = bundle(settings)
    before = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}

    with pytest.raises(BudgetExceeded):
        analyze_inputs(target, budgets={bound: 1})

    assert before == {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}


@pytest.mark.parametrize("when", ["9999-12-31T23:59:59-23:59", "0001-01-01T00:00:00+23:59"])
def test_unrepresentable_utc_event_times_remain_located_unknowns(tmp_path, when):
    settings = project(tmp_path)
    log(settings, [event("overflow", when), event("valid")])

    result = analyze(settings)

    invalid = next(e for e in result["events"] if e["item_key"] == "overflow")
    assert invalid["recorded_time"] == when
    assert invalid["time"] is None
    assert invalid["time_basis"] == "invalid_time"
    assert invalid["eligibility"] == "unknown_time"
    assert invalid["locator"]["line"] == 1
    assert result["periods"]["all_time"]["interaction_log"]["unique_papers"] == 1
    with pytest.raises(ValueError, match="split_at"):
        AnalysisBudgets(split_at=when)


def test_csv_record_budget_precedes_line_index_expansion(tmp_path, monkeypatch):
    from zotero_summarizer.services.golden.feedback_analysis.analysis import _Guard

    settings = project(tmp_path)
    settings.golden_csv_path.write_bytes(b"item_key\n" + b"\n" * 20000)
    target = bundle(settings)
    checked = []
    original = _Guard.line

    def check_line(guard, raw):
        checked.append(len(raw))
        return original(guard, raw)

    monkeypatch.setattr(_Guard, "line", check_line)
    with pytest.raises(BudgetExceeded, match="record budget"):
        analyze_inputs(target, budgets={"max_records": 1})
    assert len(checked) <= 2


def test_wire_identity_signature_and_no_model_identity_invention(tmp_path):
    settings = project(tmp_path)
    log(settings, [event("A", gate_sha="current-event-gate")])
    settings.classifier_runs_log_path.write_text(json.dumps({"run_id": "training", "ts": "2026-10-07T00:00:00Z",
                                                            "classifier": "current", "model": "not-inference"}) + "\n")

    result = analyze(settings)

    assert result.keys() == {"schema_version", "input_content_digest", "cutoff", "provenance", "revision_bounds", "config",
                             "rules", "coverage", "papers", "events", "periods", "chronological", "ranking", "gaps", "result_digest"}
    assert result["schema_version"] == 1
    assert result["result_digest"] == hashlib.sha256(canonical_bytes(
        {k: v for k, v in result.items() if k != "result_digest"})).hexdigest()
    assert len(result["input_content_digest"]) == 64
    assert result["ranking"]["reasons"] == ["immutable_exposed_ordered_slates_unavailable", "fully_timed_slate_judgments_unavailable"]
    observation = next(e for e in result["events"] if e["channel"] == "interaction_log")
    assert observation["inference_model_identity"] is None
    assert observation["event_gate_identity"] == "current-event-gate"
    assert list(inspect.signature(analyze_inputs).parameters) == ["destination", "budgets"]
    assert AnalysisBudgets().max_records == 100000


def test_preexistence_revision_cannot_supply_prior_label_to_later_decision(tmp_path):
    settings = project(tmp_path)
    feed(settings, created="2026-10-02T00:00:00Z")
    revision(settings, STABLE, "must_read", "2026-10-01T00:00:00Z")
    revision(settings, STABLE, "dont_read", "2026-10-03T00:00:00Z")

    result = analyze(settings)

    period = result["periods"]["all_time"]["journal"]
    assert period["category_changes_lower_bound"] == 0
    assert period["transitions"] == {"unknown": {"dont_read": 1}}


def test_unknown_field_and_clock_order_failure_remain_inspectable(tmp_path):
    settings = project(tmp_path)
    revision(settings, "A", "must_read", "2026-10-07T00:00:00Z")
    revision(settings, "A", "dont_read", "2026-10-06T00:00:00Z")

    result = analyze(settings)

    period = result["periods"]["all_time"]["journal"]
    assert period["category_changes_lower_bound"] == 0
    assert any(g["reason"] == "revision_time_order_conflict" for g in result["gaps"])
    assert len([e for e in result["events"] if e["channel"] == "journal"]) == 2


def test_actual_app_naive_defaults_and_aware_log_are_distinct_channels(tmp_path):
    settings = project(tmp_path)
    sql(settings, "INSERT INTO label_verdicts(item_key,user_priority,original_derived_priority,comment,source,created_at) "
        "VALUES ('A','must_read','dont_read','retained','user','2026-10-07T00:00:00Z')")
    log(settings, [event("A", model_priority="dont_read", new_user_priority="must_read")])

    result = analyze(settings)

    journal = [e for e in result["events"] if e["channel"] == "journal"]
    assert len(journal) == 1
    assert journal[0]["eligibility"] == "unknown_time"
    assert journal[0]["prediction"] is None
    assert result["periods"]["all_time"]["interaction_log"]["agreement"]["false_negative_count"] == 1
    assert result["periods"]["all_time"]["journal"]["unique_papers"] == 0


def test_naive_revision_backfill_is_not_cutoff_history(tmp_path):
    settings = project(tmp_path)
    revision(settings, "A", "must_read", "2026-10-07 00:00:00")
    revision(settings, "A", "dont_read", "2026-10-07")

    result = analyze(settings)

    assert result["periods"]["all_time"]["journal"]["unique_papers"] == 0
    assert all(e["eligibility"] == "unknown_time" for e in result["events"] if e["channel"] == "journal")
    assert any(g["reason"] == "unproven_timestamp_origin" for g in result["gaps"])


def p2_csv_bundle(tmp_path, comment="c" * 140000):
    import csv
    import io

    settings = project(tmp_path)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["item_key", "gold_label", "gold_notes"])
    writer.writerow(["PAPER001", "should_read", comment])
    writer.writerow(["PAPER002", "could_read", "second valid row"])
    settings.golden_csv_path.write_text(stream.getvalue())
    return bundle(settings)


@pytest.mark.parametrize("comment", ["c" * 140000, "π" * 140000, "c" * 600000 + "\n" + "c" * 600000],
                         ids=["ascii", "unicode", "multiline"])
def test_p2_csv_same_input_config_is_independent_of_entry_limit(tmp_path, comment):
    import csv

    target = p2_csv_bundle(tmp_path, comment)
    previous = csv.field_size_limit()
    results = []
    try:
        for ambient in (131072, 300000):
            csv.field_size_limit(ambient)
            result = analyze_inputs(target)
            assert csv.field_size_limit() == ambient
            source = result["coverage"]["sources"]["golden_csv"]
            assert source["records"] == 2 and source["malformed"] == 0
            assert not any(g["reason"] == "malformed_csv_record" for g in result["gaps"])
            rows = [e for e in result["events"] if e["channel"] == "golden_csv"]
            assert [e["comment"] for e in rows] == [comment, "second valid row"]
            assert rows[-1]["locator"]["byte_end"] == len((target / "inputs/golden.csv").read_bytes())
            assert result["config"]["max_csv_field_chars"] == 64 * 1024 * 1024
            assert result["config"]["csv_parser_ceiling_chars"] == result["config"]["max_decoded_bytes"]
            assert result["config"]["csv_effective_field_chars"] == 64 * 1024 * 1024
            results.append(result)
    finally:
        csv.field_size_limit(previous)
    assert results[0] == results[1]  # includes input/config/result digests


@pytest.mark.parametrize("field_cap", [3, 131072], ids=["header", "row"])
def test_p2_csv_named_lower_cap_is_budget_not_malformed_success(tmp_path, field_cap):
    import csv

    target = p2_csv_bundle(tmp_path)
    previous = csv.field_size_limit()
    with pytest.raises(BudgetExceeded, match="CSV field"):
        analyze_inputs(target, budgets={"max_csv_field_chars": field_cap})
    assert csv.field_size_limit() == previous


@pytest.mark.parametrize("cap", [0, True, 64 * 1024 * 1024 + 1])
def test_p2_csv_field_cap_invalid_before_bundle_io(tmp_path, cap):
    with pytest.raises(ValueError):
        analyze_inputs(tmp_path / "absent", budgets={"max_csv_field_chars": cap})


def test_p2_csv_genuine_syntax_keeps_remainder_locator_and_restores_limit(tmp_path):
    import csv

    settings = project(tmp_path)
    raw = b'item_key,gold_notes\nA,"unterminated\nremainder'
    settings.golden_csv_path.write_bytes(raw)
    previous = csv.field_size_limit()
    result = analyze(settings)
    assert csv.field_size_limit() == previous
    source = result["coverage"]["sources"]["golden_csv"]
    assert source["records"] == source["malformed"] == 1
    locator = next(g["locator"] for g in result["gaps"] if g["reason"] == "malformed_csv_record")
    assert locator["line"] == 2 and locator["line_end"] == 3
    assert locator["byte_start"] == len(b"item_key,gold_notes\n")
    assert locator["byte_end"] == len(raw)


def test_p2_csv_scope_restores_on_baseexception_and_releases_lock():
    import csv
    from zotero_summarizer.services.golden.feedback_analysis import history
    from zotero_summarizer.services.golden.feedback_analysis.analysis import _Guard

    class Cancelled(BaseException):
        pass

    previous = csv.field_size_limit()
    with pytest.raises(Cancelled):
        with history._csv_field_limit(1024 * 1024, _Guard(AnalysisBudgets())):
            assert csv.field_size_limit() == 1024 * 1024
            raise Cancelled()
    assert csv.field_size_limit() == previous
    assert history._CSV_PARSE_LOCK.acquire(timeout=0.1)
    history._CSV_PARSE_LOCK.release()


def test_p2_csv_scope_lock_wait_is_deadline_bounded():
    import csv
    from zotero_summarizer.services.golden.feedback_analysis import history
    from zotero_summarizer.services.golden.feedback_analysis.analysis import _Guard

    previous = csv.field_size_limit()
    history._CSV_PARSE_LOCK.acquire()
    try:
        with pytest.raises(BudgetExceeded, match="wall cap"):
            with history._csv_field_limit(1024 * 1024, _Guard(AnalysisBudgets(wall_cap_seconds=0.02))):
                pytest.fail("lock unexpectedly acquired")
    finally:
        history._CSV_PARSE_LOCK.release()
    assert csv.field_size_limit() == previous


def test_p2_csv_concurrent_owned_calls_have_distinct_caps_and_restore(tmp_path, monkeypatch):
    import csv
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    target = p2_csv_bundle(tmp_path)
    previous = csv.field_size_limit()
    actual_reader = csv.reader
    observed = []
    start = threading.Barrier(2)

    def observed_reader(*args, **kwargs):
        observed.append(csv.field_size_limit())
        time.sleep(0.02)  # allow the other owned call to contend for the real lock
        return actual_reader(*args, **kwargs)

    def run(cap):
        start.wait(timeout=5)
        return analyze_inputs(target, budgets={"max_decoded_bytes": cap,
                                              "max_csv_field_chars": cap // 4})

    monkeypatch.setattr(csv, "reader", observed_reader)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, cap) for cap in (1024 * 1024, 2 * 1024 * 1024)]
        results = [future.result(timeout=5) for future in futures]
    assert sorted(observed) == [1024 * 1024, 2 * 1024 * 1024]
    assert [r["config"]["csv_parser_ceiling_chars"] for r in results] == [1024 * 1024, 2 * 1024 * 1024]
    assert [r["config"]["csv_effective_field_chars"] for r in results] == [262144, 524288]
    assert all(r["coverage"]["sources"]["golden_csv"]["records"] == 2 for r in results)
    assert csv.field_size_limit() == previous


def test_p2_csv_outside_setter_refuses_without_clobbering_foreign_value(tmp_path, monkeypatch):
    import csv
    import threading

    target = p2_csv_bundle(tmp_path)
    previous = csv.field_size_limit()
    foreign = 250000
    actual_reader = csv.reader
    entered = threading.Event()
    changed = threading.Event()

    def observed_reader(*args, **kwargs):
        reader = actual_reader(*args, **kwargs)
        entered.set()
        assert changed.wait(timeout=5)
        return reader

    def outside_setter():
        assert entered.wait(timeout=5)
        csv.field_size_limit(foreign)
        changed.set()

    monkeypatch.setattr(csv, "reader", observed_reader)
    thread = threading.Thread(target=outside_setter)
    thread.start()
    try:
        with pytest.raises(RuntimeError, match="restoration unavailable"):
            analyze_inputs(target)
        assert csv.field_size_limit() == foreign
    finally:
        thread.join(timeout=5)
        csv.field_size_limit(previous)
    assert not thread.is_alive()


def test_p2_csv_interference_keeps_original_exception_context():
    import csv
    from zotero_summarizer.services.golden.feedback_analysis import history
    from zotero_summarizer.services.golden.feedback_analysis.analysis import _Guard

    class Cancelled(BaseException):
        pass

    previous = csv.field_size_limit()
    try:
        with pytest.raises(RuntimeError, match="restoration unavailable") as caught:
            with history._csv_field_limit(1024 * 1024, _Guard(AnalysisBudgets())):
                csv.field_size_limit(250000)
                raise Cancelled()
        assert isinstance(caught.value.__context__, Cancelled)
        assert csv.field_size_limit() == 250000
    finally:
        csv.field_size_limit(previous)
