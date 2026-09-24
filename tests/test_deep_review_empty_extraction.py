from tests.test_deep_review import _StubExtractor, _StubReader, _detail, _run, _wire
from zotero_summarizer.services.library import _review_cache, deep_review
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def test_empty_extraction_fails_job_instead_of_caching_ready_review(monkeypatch, tmp_path):
    with deep_review._LOCK:
        deep_review._JOBS.clear()
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: tmp_path / "deep_reviews.json")
    config = _default_goals_config()
    _wire(monkeypatch, config, reader=_StubReader({"EMPTY": _detail()}), extractor=_StubExtractor(" \n "))

    _run([{"item_key": "EMPTY", "title": "No text"}])

    status = deep_review.status("EMPTY")
    assert status["status"] == "error"
    assert "PDF extraction returned no text" in status["error"]
    assert _review_cache.get_cached_review("EMPTY") is None


def test_legacy_null_digest_recomputes_but_missing_pdf_remains_current(monkeypatch):
    from zotero_summarizer.services.library import _review_identity

    identity = {
        "generation_sha256": "a" * 64, "source_sha256": "b" * 64,
        "source_kind": "library", "source_path": "/paper.pdf", "focus_prompt": "",
    }
    monkeypatch.setattr(_review_identity, "current_review_identity", lambda _key, stored: stored)
    empty_extraction = {
        "review_contract_version": _review_cache.REVIEW_CONTRACT_VERSION,
        "review_identity": identity, "digest": None, "needs_pdf": False,
    }
    missing_pdf = {**empty_extraction, "needs_pdf": True}

    assert not _review_cache.review_is_current(empty_extraction, "EMPTY")
    assert _review_cache.review_is_current(missing_pdf, "MISSING")
