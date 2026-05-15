"""Tests for the Phase 1.16 Step 0.2 test-retest reliability framework.

Covers stratified sampling, session persistence, and metric computation
(Cohen's κ, ICC(2,1), Pearson r, Spearman ρ). No external services touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zotero_summarizer.services import relabel_audit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_row(
    item_key: str,
    *,
    days: int,
    priority: str = "should_read",
    relevance: float = 4.0,
    title: str = "T",
    abstract: str = "Some abstract text",
) -> dict[str, str]:
    return {
        "item_key": item_key,
        "title": title,
        "authors": "J. Doe",
        "venue": "Nature",
        "abstract": abstract,
        "days_since_added": str(days),
        "gold_priority_final": priority,
        "gold_inferred_relevance": str(relevance),
    }


# ---------------------------------------------------------------------------
# is_eligible_row + _build_candidate
# ---------------------------------------------------------------------------


def test_eligible_row_requires_min_age():
    assert relabel_audit.is_eligible_row(_make_row("K1", days=200)) is True
    assert relabel_audit.is_eligible_row(_make_row("K2", days=89)) is False
    assert relabel_audit.is_eligible_row(_make_row("K3", days=90)) is True


def test_eligible_row_rejects_missing_fields():
    r = _make_row("K1", days=200)
    r["title"] = ""
    assert relabel_audit.is_eligible_row(r) is False
    r = _make_row("K2", days=200)
    r["abstract"] = ""
    assert relabel_audit.is_eligible_row(r) is False
    r = _make_row("K3", days=200)
    r["gold_inferred_relevance"] = ""
    assert relabel_audit.is_eligible_row(r) is False


def test_eligible_row_rejects_bad_numerics():
    r = _make_row("K1", days=200)
    r["days_since_added"] = "abc"
    assert relabel_audit.is_eligible_row(r) is False
    r = _make_row("K2", days=200)
    r["gold_inferred_relevance"] = "not_a_float"
    assert relabel_audit.is_eligible_row(r) is False


def test_eligible_row_rejects_invalid_priority():
    r = _make_row("K1", days=200, priority="meta")
    assert relabel_audit.is_eligible_row(r) is False


def test_build_candidate_fails_fast_on_ineligible():
    r = _make_row("K1", days=50)
    with pytest.raises(ValueError, match="is_eligible_row"):
        relabel_audit._build_candidate(r)


def test_build_candidate_assigns_correct_bucket():
    r1 = _make_row("K1", days=120)
    r2 = _make_row("K2", days=200)
    r3 = _make_row("K3", days=500)
    r4 = _make_row("K4", days=900)
    assert relabel_audit._build_candidate(r1).age_bucket == "90-180"
    assert relabel_audit._build_candidate(r2).age_bucket == "180-365"
    assert relabel_audit._build_candidate(r3).age_bucket == "365-730"
    assert relabel_audit._build_candidate(r4).age_bucket == ">730"


# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------


def test_sample_stratified_returns_at_most_sample_size():
    rows = (
        [_make_row(f"K{i}", days=100, priority="must_read") for i in range(20)]
        + [_make_row(f"L{i}", days=250, priority="should_read") for i in range(20)]
        + [_make_row(f"M{i}", days=500, priority="could_read") for i in range(20)]
        + [_make_row(f"N{i}", days=900, priority="dont_read") for i in range(20)]
    )
    chosen = relabel_audit.sample_stratified(rows, sample_size=40)
    assert len(chosen) <= 40
    # All 4 buckets represented.
    buckets = {c.age_bucket for c in chosen}
    assert buckets == {"90-180", "180-365", "365-730", ">730"}


def test_sample_stratified_is_deterministic_with_seed():
    rows = [
        _make_row(f"K{i}", days=100 + (i % 800), priority="should_read")
        for i in range(50)
    ]
    a = relabel_audit.sample_stratified(rows, sample_size=12, seed=42)
    b = relabel_audit.sample_stratified(rows, sample_size=12, seed=42)
    assert [c.item_key for c in a] == [c.item_key for c in b]


def test_sample_stratified_raises_on_empty_pool():
    rows = [_make_row(f"K{i}", days=50) for i in range(10)]  # all too recent
    with pytest.raises(ValueError, match="no candidates"):
        relabel_audit.sample_stratified(rows)


def test_sample_stratified_diverse_classes_per_bucket():
    """Each bucket should hit at least 2 priority classes when input has them."""
    rows = []
    for i in range(10):
        rows.append(_make_row(f"M{i}", days=100, priority="must_read"))
        rows.append(_make_row(f"D{i}", days=100, priority="dont_read"))
        rows.append(_make_row(f"M{i}_b", days=200, priority="must_read"))
        rows.append(_make_row(f"D{i}_b", days=200, priority="dont_read"))
    chosen = relabel_audit.sample_stratified(rows, sample_size=20)
    by_bucket: dict[str, set[str]] = {}
    for c in chosen:
        by_bucket.setdefault(c.age_bucket, set()).add(c.original_priority)
    for bucket, classes in by_bucket.items():
        assert len(classes) >= 2, f"bucket {bucket} has only {classes}"


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


def test_session_round_trip(tmp_path):
    rows = [_make_row(f"K{i}", days=200, priority="must_read") for i in range(20)]
    chosen = relabel_audit.sample_stratified(rows, sample_size=8)
    session_path = tmp_path / "audit.json"
    relabel_audit.write_session(session_path, chosen, sample_size=8, seed=42)
    loaded = relabel_audit.read_session(session_path)
    assert loaded["sample_size"] == 8
    assert len(loaded["candidates"]) == len(chosen)
    assert loaded["responses"] == {}


def test_record_response_mutates_session(tmp_path):
    rows = [_make_row(f"K{i}", days=200) for i in range(10)]
    chosen = relabel_audit.sample_stratified(rows, sample_size=5)
    session_path = tmp_path / "audit.json"
    relabel_audit.write_session(session_path, chosen, sample_size=5, seed=42)

    target_key = chosen[0].item_key
    session = relabel_audit.record_response(session_path, target_key, "must_read")
    assert target_key in session["responses"]
    assert session["responses"][target_key]["new_priority"] == "must_read"
    # Reload from disk to verify persistence.
    reloaded = relabel_audit.read_session(session_path)
    assert target_key in reloaded["responses"]


def test_record_response_rejects_unknown_item(tmp_path):
    rows = [_make_row(f"K{i}", days=200) for i in range(5)]
    chosen = relabel_audit.sample_stratified(rows, sample_size=3)
    session_path = tmp_path / "audit.json"
    relabel_audit.write_session(session_path, chosen, sample_size=3, seed=42)
    with pytest.raises(ValueError, match="not in this session"):
        relabel_audit.record_response(session_path, "ALIEN_KEY", "must_read")


def test_record_response_rejects_invalid_priority(tmp_path):
    rows = [_make_row(f"K{i}", days=200) for i in range(5)]
    chosen = relabel_audit.sample_stratified(rows, sample_size=3)
    session_path = tmp_path / "audit.json"
    relabel_audit.write_session(session_path, chosen, sample_size=3, seed=42)
    with pytest.raises(ValueError, match="must be one of"):
        relabel_audit.record_response(session_path, chosen[0].item_key, "neutral")


def test_read_session_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        relabel_audit.read_session(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def test_compute_metrics_perfect_agreement():
    """Identical labels → κ = 1.0, ICC = 1.0, Pearson = 1.0."""
    responses = [
        relabel_audit.AuditResponse(
            item_key=f"K{i}",
            original_priority="must_read",
            original_inferred_relevance=5.0,
            new_priority="must_read",
            new_relevance=5.0,
            timestamp_iso="2026-05-14T10:00:00Z",
            age_bucket="180-365",
        )
        for i in range(5)
    ] + [
        relabel_audit.AuditResponse(
            item_key=f"L{i}",
            original_priority="dont_read",
            original_inferred_relevance=1.0,
            new_priority="dont_read",
            new_relevance=1.0,
            timestamp_iso="2026-05-14T10:00:00Z",
            age_bucket="180-365",
        )
        for i in range(5)
    ]
    metrics = relabel_audit.compute_metrics(responses)
    assert metrics.cohen_kappa == pytest.approx(1.0, abs=1e-6)
    assert metrics.icc_2_1 == pytest.approx(1.0, abs=1e-6)
    assert metrics.pearson_r == pytest.approx(1.0, abs=1e-6)
    assert metrics.spearman_rho == pytest.approx(1.0, abs=1e-6)


def test_compute_metrics_complete_disagreement():
    """Opposite labels every time → κ < 0; ICC near 0 or negative."""
    responses = [
        relabel_audit.AuditResponse(
            item_key=f"K{i}",
            original_priority="must_read",
            original_inferred_relevance=5.0,
            new_priority="dont_read",
            new_relevance=1.0,
            timestamp_iso="2026-05-14T10:00:00Z",
            age_bucket="180-365",
        )
        for i in range(5)
    ] + [
        relabel_audit.AuditResponse(
            item_key=f"L{i}",
            original_priority="dont_read",
            original_inferred_relevance=1.0,
            new_priority="must_read",
            new_relevance=5.0,
            timestamp_iso="2026-05-14T10:00:00Z",
            age_bucket="180-365",
        )
        for i in range(5)
    ]
    metrics = relabel_audit.compute_metrics(responses)
    assert metrics.cohen_kappa < 0.0
    # ICC for opposite scores has a negative correlation component.
    assert metrics.pearson_r < 0.0


def test_compute_metrics_empty_raises():
    with pytest.raises(ValueError, match="zero responses"):
        relabel_audit.compute_metrics([])


def test_compute_metrics_partial_agreement_known_values():
    """Hand-computed κ for a 2-class confusion matrix."""
    # 8 must→must, 2 must→dont, 1 dont→must, 9 dont→dont
    pairs = (
        [("must_read", "must_read")] * 8
        + [("must_read", "dont_read")] * 2
        + [("dont_read", "must_read")] * 1
        + [("dont_read", "dont_read")] * 9
    )
    responses = [
        relabel_audit.AuditResponse(
            item_key=f"K{i}",
            original_priority=orig,
            original_inferred_relevance=5.0 if orig == "must_read" else 1.0,
            new_priority=new,
            new_relevance=5.0 if new == "must_read" else 1.0,
            timestamp_iso="2026-05-14T10:00:00Z",
            age_bucket="180-365",
        )
        for i, (orig, new) in enumerate(pairs)
    ]
    metrics = relabel_audit.compute_metrics(responses)
    # Observed agreement = 17/20 = 0.85
    # Expected agreement (must=10/20, must_new=9/20): 0.5*0.45 + 0.5*0.55 = 0.5
    # κ = (0.85 - 0.5) / (1 - 0.5) = 0.7
    assert metrics.cohen_kappa == pytest.approx(0.7, abs=0.01)


# ---------------------------------------------------------------------------
# ICC math
# ---------------------------------------------------------------------------


def test_icc_2_1_perfect_agreement():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert relabel_audit._icc_2_1(a, b) == pytest.approx(1.0, abs=1e-6)


def test_icc_2_1_unequal_lengths_raises():
    with pytest.raises(ValueError, match="same length"):
        relabel_audit._icc_2_1([1.0, 2.0], [1.0])


def test_icc_2_1_constant_input_raises():
    with pytest.raises(ValueError, match="all scores identical"):
        relabel_audit._icc_2_1([3.0, 3.0, 3.0], [3.0, 3.0, 3.0])


# ---------------------------------------------------------------------------
# responses_from_session pairing
# ---------------------------------------------------------------------------


def test_responses_from_session_pairs_correctly(tmp_path):
    rows = [
        _make_row(f"K{i}", days=200, priority="must_read", relevance=5.0)
        for i in range(5)
    ]
    chosen = relabel_audit.sample_stratified(rows, sample_size=3)
    session_path = tmp_path / "s.json"
    relabel_audit.write_session(session_path, chosen, sample_size=3, seed=42)
    relabel_audit.record_response(session_path, chosen[0].item_key, "dont_read")
    relabel_audit.record_response(session_path, chosen[1].item_key, "must_read")
    session = relabel_audit.read_session(session_path)
    responses = relabel_audit.responses_from_session(session)
    assert len(responses) == 2
    # Verify the original labels are preserved.
    assert all(r.original_priority == "must_read" for r in responses)
    assert {r.new_priority for r in responses} == {"dont_read", "must_read"}


def test_responses_from_session_handles_no_responses(tmp_path):
    rows = [_make_row(f"K{i}", days=200) for i in range(5)]
    chosen = relabel_audit.sample_stratified(rows, sample_size=3)
    session_path = tmp_path / "s.json"
    relabel_audit.write_session(session_path, chosen, sample_size=3, seed=42)
    session = relabel_audit.read_session(session_path)
    assert relabel_audit.responses_from_session(session) == []


# ---------------------------------------------------------------------------
# Serialization + I/O
# ---------------------------------------------------------------------------


def test_metrics_to_dict_is_json_serializable():
    responses = [
        relabel_audit.AuditResponse(
            item_key=f"K{i}",
            original_priority="must_read" if i % 2 else "dont_read",
            original_inferred_relevance=5.0 if i % 2 else 1.0,
            new_priority="must_read" if i % 2 else "dont_read",
            new_relevance=5.0 if i % 2 else 1.0,
            timestamp_iso="2026-05-14T10:00:00Z",
            age_bucket="180-365",
        )
        for i in range(10)
    ]
    metrics = relabel_audit.compute_metrics(responses)
    d = relabel_audit.metrics_to_dict(metrics)
    text = json.dumps(d)
    parsed = json.loads(text)
    assert parsed["type"] == "relabel_audit_metrics"
    assert parsed["n_paired"] == 10
    assert parsed["cohen_kappa"] == pytest.approx(1.0, abs=1e-6)


def test_load_golden_rows_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        relabel_audit.load_golden_rows(tmp_path / "nope.csv")


def test_load_golden_rows_reads_csv(tmp_path):
    csv_path = tmp_path / "g.csv"
    csv_path.write_text(
        "item_key,title,abstract,days_since_added,gold_priority_final,gold_inferred_relevance\n"
        "K1,Paper,Abstract,200,must_read,5.0\n",
        encoding="utf-8",
    )
    rows = relabel_audit.load_golden_rows(csv_path)
    assert len(rows) == 1
    assert rows[0]["item_key"] == "K1"
