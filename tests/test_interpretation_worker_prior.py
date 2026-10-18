"""Owned worker/prior publication cohort; authored only, not a runtime verdict."""
from unittest.mock import Mock

import pytest

from tests.test_interpretation_binding import C, config, usable_review
from tests.test_interpretation_producer import _base, _card, _payload
from tests.test_interpretation_review_one import paper_io
from tests.test_review_cache import _isolate_cache
from tests.test_review_source_boundary import _Mapper
from zotero_summarizer.services.library import _digest_audit, _review_cache, deep_review


@pytest.mark.parametrize("interpretation_schema,stale,retarget_ctx", [
    (None, False, False), (C, False, False), (None, True, False), (C, True, False),
    (C, False, True),
], ids=["off-current", "c-current", "off-stale", "c-stale", "c-current-retargeted-ctx"])
def test_worker_no_pdf_preserves_current_prior_but_can_replace_stale_prior(
    config, paper_io, monkeypatch, interpretation_schema, stale, retarget_ctx,
):
    key = "LIBR0038"
    condition = {"interpretation_schema": interpretation_schema} if interpretation_schema is not None else {}
    config.quality_review.chunk_strategy = "rank"
    generator, mapper = Mock(), _Mapper("Generation context only.")
    generator.pydantic_prompt.return_value = _payload(_card()) if condition else _base()
    original_binder = _digest_audit.bind_cached_audit
    binder = Mock(wraps=original_binder)
    rebuild = Mock()
    monkeypatch.setattr(_digest_audit, "bind_cached_audit", binder)
    monkeypatch.setattr(deep_review, "_try_rebuild_render", rebuild)
    monkeypatch.setattr(deep_review, "_JOBS", {})
    ctx = {
        "reader": paper_io.reader, "config": config, "extractor": paper_io.extractor,
        "llm": generator, "llm_map": mapper, "quality_enabled": True,
        "provenance": {"fixture": "owned-worker-prior"}, **condition,
    }

    # Real generation, verification, policy, source guard and native audit binding.
    prior = deep_review._review_one(
        {"item_key": key, "pdf_path": str(paper_io.source)}, focus_prompt="", **ctx,
    )
    assert prior is not None and prior["digest"] is not None and prior["needs_pdf"] is False
    assert prior["review_identity"]["source_kind"] == "override"
    assert prior["review_identity"]["source_path"] == str(paper_io.source)
    assert binder.call_count == 1 and binder.call_args.kwargs == condition
    assert _digest_audit.cached_audit_status(prior, **condition) == ("available", "")
    audit = prior["verification_audit"]
    assert audit["identity"]["review_source_sha256"] == prior["review_identity"]["source_sha256"]
    if condition:
        assert prior["review_identity"]["interpretation_schema"] == C
        assert prior["interpretation"]["native_support"]["base_digest_sha256"] == audit["identity"]["verified_digest_sha256"]
        assert audit["verified_interpretation"] == prior["interpretation"]
        assert prior["zotero_note_written"] is False
        paper_io.mirror.assert_not_called()
        paper_io.writer.assert_not_called()
    else:
        assert "interpretation_schema" not in prior["review_identity"]
        assert "interpretation" not in prior and "interpretation_evidence" not in audit
        assert prior["zotero_note_written"] is False
        paper_io.mirror.assert_not_called()
        paper_io.writer.assert_not_called()
    assert prior["zotero_note_error"] is None

    # The existing cache fixture confines every real cache write to this tempstore.
    path = _review_cache._cache_path()
    assert path.parent == paper_io.source.parent and not path.exists()
    _review_cache._write_one(key, prior)
    cache_before = path.read_bytes(), path.stat().st_mtime_ns
    assert _review_cache._read_all(readonly=True) == {key: prior}
    assert _review_cache.review_is_current(prior, key, **condition)
    view = _review_cache.get_current_review(key, **condition)
    assert view is not None and usable_review(view)
    assert view["digest"] == prior["digest"]
    if condition:
        assert view["interpretation_qualification"]["status"] == "available"
        assert view["interpretation"] == prior["interpretation"]
        assert _review_cache.get_current_review(key) is None  # OFF must not expose C.
    assert (path.read_bytes(), path.stat().st_mtime_ns) == cache_before

    # Missing the next PDF link is not deletion of the prior's override source.
    source_before = paper_io.source.read_bytes()
    paper_io.reader.get_item_detail.return_value["pdf_path"] = ""
    paper_io.reader.get_item_detail.return_value["has_pdf"] = False
    if stale:
        replacement = b"R" * len(source_before)
        assert replacement != source_before
        paper_io.source.write_bytes(replacement)
    expected_source = replacement if stale else source_before
    assert paper_io.source.is_file() and paper_io.source.read_bytes() == expected_source
    assert _review_cache.review_is_current(prior, key, **condition) is (not stale)
    expected_audit = ("stale", "source_bytes_changed") if stale else ("available", "")
    assert _digest_audit.cached_audit_status(prior, **condition) == expected_audit
    view = _review_cache.get_current_review(key, **condition)
    if stale:
        assert view is None and not usable_review(view)
    else:
        assert view is not None and usable_review(view)
        if condition:
            assert view["interpretation_qualification"]["status"] == "available"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == cache_before
    paper_io.reader.reset_mock()

    if retarget_ctx:
        assert interpretation_schema == C and not stale

        def bind_then_retarget(*args, **kwargs):
            bound = original_binder(*args, **kwargs)
            ctx["interpretation_schema"] = None
            return bound

        binder.side_effect = bind_then_retarget

    # Existing CODE ctx selects C; OFF omits the keyword. No start/build_ctx shim.
    deep_review._review_worker({"item_key": key}, ctx, "")

    if retarget_ctx:
        assert ctx["interpretation_schema"] is None
    job = deep_review.status(key)
    assert job["status"] == "ready" and job["total"] == job["completed"] == 1
    assert job["error"] is None and job["diagnostic"] is None and job["progress"] == {}
    terminal = [event for event in job["attempt"]["events"] if event["stage"] == "terminal"]
    assert len(terminal) == 1 and terminal[0]["kind"] == "identity"
    assert terminal[0]["identity"] == {
        "outcome": "source_unavailable", "counts_as_verified_review": False,
        "availability": "not_observed",
    }
    assert job["attempt"]["stage_counts"]["terminal:identity"] == 1
    assert binder.call_count == 2 and binder.call_args.kwargs == condition
    candidate = binder.call_args.args[0]
    assert candidate["needs_pdf"] is True and candidate["digest"] is None
    assert candidate["verification_audit"] is None
    assert candidate["review_identity"]["source_kind"] == "none"
    assert candidate["review_identity"]["source_path"] == ""
    assert candidate["zotero_note_written"] is False and candidate["zotero_note_error"] is None
    assert "interpretation" not in candidate
    if condition:
        assert candidate["review_identity"]["interpretation_schema"] == C
    else:
        assert "interpretation_schema" not in candidate["review_identity"]
    paper_io.reader.get_item_detail.assert_called_once_with(key)
    paper_io.extractor.extract_text.assert_called_once_with(str(paper_io.source))
    paper_io.layers.assert_called_once()
    assert generator.pydantic_prompt.call_count == mapper.calls == 1
    assert mapper.map_calls == 0
    assert paper_io.mirror.call_count == paper_io.writer.call_count == 0
    assert paper_io.source.read_bytes() == expected_source

    stored = _review_cache._read_all(readonly=True)
    assert set(stored) == {key}
    if stale:
        assert path.read_bytes() != cache_before[0]
        placeholder = stored[key]
        assert placeholder != prior and placeholder["digest"] is None and placeholder["needs_pdf"] is True
        assert not usable_review(placeholder) and placeholder["verification_audit"] is None
        assert "interpretation" not in placeholder
        assert placeholder["review_identity"] == candidate["review_identity"]
        assert not _review_cache.review_is_current(prior, key, **condition)
        rebuild.assert_called_once_with(key)
    else:
        assert (path.read_bytes(), path.stat().st_mtime_ns) == cache_before
        assert stored[key] == prior
        rebuild.assert_not_called()
        view = _review_cache.get_current_review(key, **condition)
        assert view is not None and usable_review(view)
        if condition:
            assert view["interpretation_qualification"]["status"] == "available"
            assert view["interpretation"] == prior["interpretation"]
        assert (path.read_bytes(), path.stat().st_mtime_ns) == cache_before
