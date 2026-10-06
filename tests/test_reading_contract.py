"""Positive controls and corrupt-candidate controls for saved-field preservation."""

import html

import pytest

from tests._reading_contract import Document, aggregate, canonical_text, check_html
from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.services.zotero._notes import build_triage_note_html


def run(tmp_path, source, markup, checks):
    artifact = tmp_path / "captured.html"
    artifact.write_text(markup, encoding="utf-8")
    receipts = check_html(source, markup, checks, evidence_path=artifact)
    return aggregate([check["id"] for check in checks], receipts), receipts


@pytest.fixture
def note_case():
    caveat = "Dr. Example reports -3.14; not clinically validated. " + "detail " * 90 + "Critical tail."
    source = {"limitations": caveat, "methods": "https://example.org/v1.2?q=-3.14&mode=full"}
    summary = SummarizeResponse(relevance_score=4, executive_summary="Overview.", triage_rationale="Fit.", **source)
    markup = build_triage_note_html("Paper", summary)
    checks = [{"id": field, "source_id": (field,), "selector": {"heading": heading}, "mode": "contains"}
              for field, heading in [("limitations", "Limitations / uncertainty"), ("methods", "Approach / methods")]]
    return source, markup, checks


def test_real_note_positive_receipts(tmp_path, note_case):
    source, markup, checks = note_case
    report, receipts = run(tmp_path, source, markup, checks)
    assert report == {"status": "PASS", "total": 2, "covered": 2, "missing": [], "unknown": []}
    assert receipts[0]["expected"] == source["limitations"]
    assert receipts[0]["source_id"] == ["limitations"]
    assert len(receipts[0]["evidence_sha256"]) == 64
    assert receipts[0]["evidence_path"].endswith("captured.html")


@pytest.mark.parametrize("old,new", [
    ("not clinically validated.", "clinically validated."),
    ("Critical tail.", ""), ("-3.14", "3.14"), ("3.14", "3.15"),
    ("v1.2?q=", "v1.3?q="),
])
def test_real_note_mutation_rejected(tmp_path, note_case, old, new):
    source, markup, checks = note_case
    corrupted = markup.replace(old, new)
    assert corrupted != markup
    report, receipts = run(tmp_path, source, corrupted, checks)
    assert report["status"] == "FAIL"
    assert any(receipt["status"] == "FAIL" for receipt in receipts)


def test_truncation_after_abbreviation_and_critical_caveat_drop(tmp_path, note_case):
    source, markup, checks = note_case
    for replacement in ["Dr.", ""]:
        corrupted = markup.replace(html.escape(source["limitations"]), replacement)
        report, _ = run(tmp_path, source, corrupted, checks)
        assert report["status"] == "FAIL"


def test_goal_quote_association_controls(tmp_path):
    source = {"goals": [{"goal": "First goal", "quote": "Only 12 participants."},
                        {"goal": "Second goal", "quote": "No external validation."}]}
    markup = '<div title="First goal"><blockquote>Only 12 participants.</blockquote></div>' \
             '<div title="Second goal"><blockquote>No external validation.</blockquote></div>'
    checks = [{"id": f"quote-{index}", "source_id": ("goals", index, "quote"),
               "scope": {"attrs": {"title": goal["goal"]}}, "selector": {"tag": "blockquote"},
               "mode": "exact"} for index, goal in enumerate(source["goals"])]
    assert run(tmp_path, source, markup, checks)[0]["status"] == "PASS"
    swapped = markup.replace("First goal", "temporary").replace("Second goal", "First goal").replace("temporary", "Second goal")
    dropped = markup.replace("Only 12 participants.", "")
    for corrupted in [swapped, dropped]:
        assert run(tmp_path, source, corrupted, checks)[0]["status"] == "FAIL"


def test_single_grade_count_duplicate_rejected(tmp_path):
    source = {"quality": {"grade": "B"}}
    checks = [{"id": "grade", "source_id": ("quality", "grade"),
               "selector": {"attrs": {"class": "grade"}}, "mode": "count", "expected": 1}]
    markup = '<span class="grade">B</span>'
    assert run(tmp_path, source, markup, checks)[0]["status"] == "PASS"
    assert run(tmp_path, source, markup + markup, checks)[0]["status"] == "FAIL"


def test_empty_legacy_digest_keeps_prior_assessment(tmp_path):
    source = {"digest": {}, "quality": {"verdict": "Prior assessment."}}
    checks = [{"id": "legacy", "source_id": ("quality", "verdict"), "selector": {"tag": "p"}, "mode": "exact"}]
    assert run(tmp_path, source, "<p>Prior assessment.</p>", checks)[0]["status"] == "PASS"
    assert run(tmp_path, source, "<p></p>", checks)[0]["status"] == "FAIL"


@pytest.mark.parametrize("gold,markup", [(False, "<p>False</p>"), ("", "<p></p>")])
def test_falsey_gold_is_checked(tmp_path, gold, markup):
    checks = [{"id": "value", "source_id": ("value",), "selector": {"tag": "p"}, "mode": "exact"}]
    assert run(tmp_path, {"value": gold}, markup, checks)[0]["status"] == "PASS"
    assert run(tmp_path, {"value": gold}, "<p>invented</p>", checks)[0]["status"] == "FAIL"


def test_attribute_url_exact_and_escaping(tmp_path):
    source = {"url": "https://example.org/?a=-1&b=2"}
    checks = [{"id": "url", "source_id": ("url",), "selector": {"tag": "a"}, "mode": "attribute", "attribute": "href"}]
    markup = f'<a href="{html.escape(source["url"])}">Code</a>'
    assert run(tmp_path, source, markup, checks)[0]["status"] == "PASS"
    assert run(tmp_path, source, markup.replace("b=2", "b=3"), checks)[0]["status"] == "FAIL"


def test_parser_only_normalizes_whitespace_and_escaping():
    doc = Document('<p>Dr. <b>Example</b>: -3.14 &amp; not true.</p><script>invented</script>')
    assert canonical_text(doc.root.text()) == "Dr. Example: -3.14 & not true."
    assert canonical_text(Document('<p>neg<b>ation</b></p>').root.text()) == "negation"


@pytest.mark.parametrize("checks", [[], [{"id": "", "source_id": ("x",), "selector": {"tag": "p"}}],
    [{"id": "x", "source_id": (), "selector": {"tag": "p"}}],
    [{"id": "x", "source_id": ("x",), "selector": {"tag": "p"}, "expected": float("nan")}],
    [{"id": "x", "source_id": ("x",), "selector": {"tag": "p"}, "mode": "unknown"}],
])
def test_malformed_contract_fails_loud(tmp_path, checks):
    with pytest.raises(ValueError):
        check_html({}, "", checks, evidence_path=tmp_path / "none")


def test_duplicate_contract_ids_fail_loud(tmp_path):
    check = {"id": "same", "source_id": ("x",), "selector": {"tag": "p"}}
    with pytest.raises(ValueError):
        check_html({}, "", [check, check], evidence_path=tmp_path / "none")


def test_missing_source_and_evidence_block(tmp_path):
    checks = [{"id": "x", "source_id": ("x",), "selector": {"tag": "p"}}]
    assert run(tmp_path, {}, "<p>present</p>", checks)[0]["status"] == "BLOCKED"
    receipts = check_html({"x": "present"}, "<p>present</p>", checks, evidence_path=tmp_path / "absent")
    assert receipts[0]["error_code"] == "missing_or_mismatched_evidence"
    path = tmp_path / "wrong"
    path.write_text("other")
    assert check_html({"x": "present"}, "<p>present</p>", checks, evidence_path=path)[0]["status"] == "BLOCKED"


@pytest.mark.parametrize("receipts,status", [([], "BLOCKED"),
    ([{"check_id": "a", "status": "PASS"}], "BLOCKED"),
    ([{"check_id": "a", "status": "PASS"}, {"check_id": "b", "status": "PASS"}], "PASS"),
    ([{"check_id": "a", "status": "FAIL"}, {"check_id": "b", "status": "PASS"}], "FAIL"),
    ([{"check_id": "a", "status": "PASS"}, {"check_id": "b", "status": "UNKNOWN"}], "BLOCKED"),
    ([{"check_id": "a", "status": "PASS"}, {"check_id": "b", "status": "PASS"}, {"check_id": "extra", "status": "PASS"}], "BLOCKED"),
])
def test_strict_required_aggregation(receipts, status):
    assert aggregate(["a", "b"], receipts)["status"] == status


def test_aggregate_rejects_empty_and_duplicate_ids():
    for ids in [[], [""], ["a", "a"]]:
        with pytest.raises(ValueError):
            aggregate(ids, [])
    with pytest.raises(ValueError):
        aggregate(["a"], [{"check_id": "a", "status": "PASS"}] * 2)
