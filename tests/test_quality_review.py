"""Full-text quality review: prompt mapping, OA-PDF fetch contract, persistence
parse, and the backlog count."""
from __future__ import annotations

import sqlite3

import pytest

from zotero_summarizer.models import QualityReview
from zotero_summarizer.services.library import quality_review as qr
from zotero_summarizer.services._common import read_config, settings as _settings
from zotero_summarizer.services.triage.daily_select import _candidate as cand
from zotero_summarizer.storage import feeds as fs


@pytest.fixture(scope="module")
def config():
    return read_config(_settings().config_path)


class _StubLLM:
    def __init__(self):
        self.last_prompt = None

    def pydantic_prompt(self, *, prompt, pydantic_model):
        self.last_prompt = prompt
        return pydantic_model(
            grade="A", soundness=5, novelty=4, significance=4,
            reproducibility=3, clarity=4, verdict="Strong.",
            key_strength="s", key_weakness="w", confidence=0.9,
        )


class _StubExtractor:
    def __init__(self, text):
        self._t = text

    def extract_text(self, pdf_path):
        return self._t


def test_assess_quality_maps_and_truncates(config):
    llm = _StubLLM()
    cap = config.quality_review.max_text_chars
    long_text = "~" * (cap + 5000)  # '~' does not appear in the prompt template
    rev = qr.assess_quality(title="T", full_text=long_text, config=config, llm=llm)
    assert rev.grade == "A" and rev.basis == "full_text"
    assert llm.last_prompt.count("~") == cap  # full text truncated to the cap


def test_fetch_full_text_none_without_ids(config):
    # No arxiv_id / doi / url → no OA PDF → the documented None contract.
    assert qr.fetch_full_text({"title": "t"}, config=config, extractor=_StubExtractor("x")) is None


def test_fetch_full_text_reads_pdf(config, monkeypatch, tmp_path):
    monkeypatch.setattr(qr, "resolve_pdf_url", lambda **k: "http://x/p.pdf")
    monkeypatch.setattr(qr, "fetch_pdf", lambda url, **k: tmp_path / "p.pdf")
    text = qr.fetch_full_text(
        {"arxiv_id": "2604.00001"}, config=config, extractor=_StubExtractor("FULLTEXT BODY"),
    )
    assert text == "FULLTEXT BODY"


def test_review_row_not_assessed_without_pdf(config):
    rev = qr.review_row({"title": "t"}, config=config, llm=_StubLLM(), extractor=_StubExtractor("x"))
    assert rev.grade == "" and rev.basis == "not_assessed"


def test_review_row_full_path(config, monkeypatch, tmp_path):
    monkeypatch.setattr(qr, "resolve_pdf_url", lambda **k: "http://x/p.pdf")
    monkeypatch.setattr(qr, "fetch_pdf", lambda url, **k: tmp_path / "p.pdf")
    rev = qr.review_row(
        {"arxiv_id": "2604.00001", "title": "T"},
        config=config, llm=_StubLLM(), extractor=_StubExtractor("BODY"),
    )
    assert rev.grade == "A" and rev.basis == "full_text"


def test_quality_review_grade_normalization():
    assert QualityReview(grade="a").grade == "A"
    assert QualityReview(grade="B)").grade == "B"
    assert QualityReview(grade="x").grade == ""


def test_row_quality_parse_and_contract():
    assert cand.row_quality({"quality_review_json": ""}) == {}
    assert cand.row_quality({"quality_review_json": '{"grade":"B"}'})["grade"] == "B"
    with pytest.raises(ValueError):
        cand.row_quality({"quality_review_json": "[1,2]"})  # JSON, but not an object


def test_count_by_decisions(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.row_factory = sqlite3.Row
    try:
        fs.init_feeds_schema(conn)
        for i in (1, 2, 3):
            fs.record_decision(
                conn, run_id="r",
                feed_item={"feed_library_id": 1, "item_id": i, "guid": f"g{i}", "title": f"P{i}"},
                decision=fs.DECISION_TRIAGED_PENDING, composite_score=1.0,
            )
        fs.record_decision(
            conn, run_id="r",
            feed_item={"feed_library_id": 1, "item_id": 9, "guid": "g9", "title": "P9"},
            decision=fs.DECISION_USER_REJECTED, composite_score=1.0,
        )
        conn.commit()
        assert fs.count_by_decisions(conn, [fs.DECISION_TRIAGED_PENDING]) == 3
        assert fs.count_by_decisions(conn, []) == 0
    finally:
        conn.close()
