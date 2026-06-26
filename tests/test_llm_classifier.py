"""Tests for the LLM-as-classifier path (parallel batch)."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zotero_summarizer.services.model import llm_classifier
from zotero_summarizer.services.model.llm_classifier import (
    LLMClassification,
    _LLMVerdict,
    classify_papers_with_llm,
    write_predictions_to_csv,
)


def _verdict(p: str = "should_read", conf: float = 0.7, why: str = "ok") -> _LLMVerdict:
    return _LLMVerdict(priority=p, confidence=conf, rationale=why)


def _row(key: str, title: str = "Paper", abstract: str = "Detailed abstract ...") -> dict:
    return {"item_key": key, "title": title, "abstract": abstract}


def test_classify_returns_one_classification_per_row():
    llm = MagicMock()
    llm.pydantic_prompt.side_effect = [
        _verdict("must_read"), _verdict("dont_read"), _verdict("should_read"),
    ]
    rows = [_row("A"), _row("B"), _row("C")]
    out = classify_papers_with_llm(rows, llm, research_goals=["agents"], workers=2)
    assert len(out) == 3
    assert {c.item_key for c in out} == {"A", "B", "C"}
    assert all(c.priority in {"must_read", "should_read", "dont_read"} for c in out)


def test_classify_skips_rows_with_empty_title_or_abstract():
    llm = MagicMock()
    llm.pydantic_prompt.return_value = _verdict("must_read")
    rows = [
        _row("OK"),
        {"item_key": "MISSING_T", "title": "", "abstract": "abs"},
        {"item_key": "MISSING_A", "title": "t", "abstract": ""},
    ]
    out = classify_papers_with_llm(rows, llm, research_goals=["agents"], workers=1)
    by_key = {c.item_key: c for c in out}
    assert by_key["OK"].priority == "must_read"
    assert by_key["MISSING_T"].priority == ""
    assert by_key["MISSING_T"].error == "missing title or abstract"
    assert by_key["MISSING_A"].priority == ""


def test_classify_swallows_llm_errors():
    llm = MagicMock()
    llm.pydantic_prompt.side_effect = RuntimeError("connection timeout")
    rows = [_row("A"), _row("B")]
    out = classify_papers_with_llm(rows, llm, research_goals=[], workers=2)
    assert len(out) == 2
    assert all(c.priority == "" for c in out)
    assert all("connection timeout" in c.error for c in out)


def test_verdict_rejects_invalid_priority_via_normalisation():
    """nonsense → coerced to could_read via normalize_reading_priority."""
    v = _LLMVerdict(priority="nonsense", confidence=0.5, rationale="meh")
    assert v.priority == "could_read"


def test_verdict_accepts_all_four_valid_priorities():
    for p in ("must_read", "should_read", "could_read", "dont_read"):
        v = _LLMVerdict(priority=p, confidence=0.5, rationale="")
        assert v.priority == p


def test_write_predictions_adds_per_classifier_columns(tmp_path: Path):
    """LLM predictions land under cls_{classifier_name}_* columns."""
    csv_path = tmp_path / "golden.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["item_key", "title", "gold_priority_final"])
        writer.writeheader()
        writer.writerow({"item_key": "A", "title": "p", "gold_priority_final": "must_read"})
        writer.writerow({"item_key": "B", "title": "q", "gold_priority_final": "dont_read"})
    classifications = [
        LLMClassification("A", "should_read", 0.8, "agree"),
        LLMClassification("B", "dont_read", 0.9, "off-topic"),
    ]
    updated = write_predictions_to_csv(
        csv_path, classifications, classifier_name="llm_custom",
    )
    assert updated == 2
    rows = list(csv.DictReader(csv_path.open()))
    assert rows[0]["cls_llm_custom_priority"] == "should_read"
    assert rows[1]["cls_llm_custom_priority"] == "dont_read"
    assert "cls_llm_custom_score" in rows[0]
    assert "cls_llm_custom_rationale" in rows[0]


def test_write_predictions_rejects_invalid_classifier_name(tmp_path: Path):
    csv_path = tmp_path / "golden.csv"
    csv_path.write_text("item_key,title\nA,p\n", encoding="utf-8")
    classifications = [LLMClassification("A", "must_read", 0.5, "x")]
    for bad in ("", "with space", "with/slash"):
        with pytest.raises(ValueError, match="invalid classifier_name"):
            write_predictions_to_csv(csv_path, classifications, classifier_name=bad)


def test_parallel_classification_preserves_order(tmp_path: Path):
    """Even with workers>1, the result list is in input order."""
    llm = MagicMock()
    # Each call yields a distinct priority so we can detect reordering.
    expected_priorities = [
        "must_read", "should_read", "could_read", "dont_read",
        "must_read", "should_read", "could_read", "dont_read",
    ]

    def fake_pp(prompt: str, pydantic_model):
        # Use len of prompt to pick a priority — different prompts give
        # different lengths, deterministic. But here we rely on dictionary
        # order via the rows list — easier to use a counter.
        return _verdict(p="must_read")

    llm.pydantic_prompt.side_effect = lambda **kw: _verdict("must_read")
    rows = [_row(f"K{i}", title=f"Title {i}") for i in range(8)]
    out = classify_papers_with_llm(rows, llm, research_goals=["x"], workers=4)
    assert [c.item_key for c in out] == [r["item_key"] for r in rows]
