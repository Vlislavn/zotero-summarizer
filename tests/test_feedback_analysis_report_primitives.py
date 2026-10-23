"""Report data must remain literal code, including hostile Markdown delimiters."""
from __future__ import annotations

import re

import pytest

from zotero_summarizer.services.golden.feedback_analysis import report


def test_report_declares_its_existing_callable_library_boundary():
    assert set(report.__all__) == {
        "hash_file", "inventory", "make_immutable", "markdown", "read_protocol",
        "stage_report", "verify_report", "write_private", "write_protocol",
    }
    assert all(callable(getattr(report, name)) for name in report.__all__)


@pytest.mark.parametrize("value", [
    "ordinary", "` [click](https://attacker.example) `", "`` [click](https://attacker.example) ``",
    "`one``two```three", "<img src=x onerror=alert(1)>", "a\\b\nc\rd", {"` [click](https://attacker.example) `": 1},
])
def test_evidence_uses_a_code_delimiter_absent_from_the_recorded_value(value):
    text = str(value).replace("\n", " ").replace("\r", " ")
    marker = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)

    assert report._shown(value) == f"{marker} {text} {marker}"


def test_markdown_protects_each_formatted_evidence_location():
    hostile = "` [click](https://attacker.example) `"
    result = {
        "cutoff": "2026-10-08T00:00:00Z",
        "coverage": {"unique_current_paper_groups": 1, "date_coverage": hostile, "exclusions": hostile},
        "gaps": [{"reason": hostile, "locator": hostile}],
        "periods": {"all_time": {}},
        "input_content_digest": "0" * 64, "result_digest": "1" * 64,
    }
    for channel in ("journal", "interaction_log", "current_feedback"):
        result["periods"]["all_time"][channel] = {
            "status": "present", "unique_papers": 1, "class_support": {},
            "transitions": hostile, "agreement": hostile, "ordinal_error": hostile,
            "disagreements": [hostile],
        }

    output = report.markdown(result, "published")

    assert output.count(f"`` {hostile} ``") == 16
    assert "\\`" not in output
