"""Private one-paper machine binding; scripted native checks are not human warrant."""
import builtins
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_deep_review import _detail
from tests.test_digest_verification import _claims
from tests.test_interpretation_binding import C, TEXT, _assert_base_only, config
from tests.test_interpretation_producer import InterpretationDigestResponse, _base, _card, _payload
from tests.test_interpretation_verification import _table
from tests.test_review_cache import _isolate_cache
from tests.test_review_source_boundary import _Mapper
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import _digest_audit, _map_reduce, _review_cache, _review_identity, deep_review
from zotero_summarizer.services.library._digest_verification import DigestSourceRejected, DigestVerifierUnavailable
from zotero_summarizer.services.zotero import zotero as zotero_svc


@pytest.fixture
def paper_io(tmp_path, monkeypatch):
    source = tmp_path / "owned-one-paper.pdf"
    text = TEXT.strip()
    source.write_text(text, encoding="utf-8")
    reader, extractor = Mock(), Mock()
    reader.get_item_detail.return_value = _detail(title="Owned paper", pdf_path=str(tmp_path / "other-attachment.pdf"))
    extractor.extract_text.return_value = text
    layers = Mock(return_value=(None, None, None, None, None))
    mirror = Mock(wraps=deep_review._mirror_digest_note)
    writer = Mock()
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers", layers)
    monkeypatch.setattr(deep_review, "_mirror_digest_note", mirror)
    monkeypatch.setattr(zotero_svc, "zotero_upsert_digest_note", writer)
    monkeypatch.setattr(deep_review, "now_iso_z", lambda: "2026-10-14T00:00:00Z")
    return SimpleNamespace(source=source, text=text, reader=reader, extractor=extractor,
                           layers=layers, mirror=mirror, writer=writer)


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce"])
@pytest.mark.parametrize("lean_tier", [False, True], ids=["full", "lean"])
def test_private_c_native_entry_binds_same_collector_source_and_readonly_cache(config, paper_io, monkeypatch, strategy, lean_tier):
    config.quality_review.chunk_strategy = strategy
    mapper, generator = _Mapper("Generation context only."), Mock()
    generator.pydantic_prompt.return_value = _payload(_card())
    relay = Mock(wraps=_map_reduce.digest_for_strategy)
    assessment = Mock(wraps=_map_reduce.assess_digest)
    identity = Mock(wraps=_review_identity.build_review_identity)
    binder = Mock(wraps=_digest_audit.bind_cached_audit)
    guard = Mock(wraps=_review_identity.assert_review_source_unchanged)
    trace = Mock()
    trace.attach_mock(guard, "guard")
    trace.attach_mock(paper_io.mirror, "mirror")
    trace.attach_mock(binder, "bind")
    monkeypatch.setattr(_map_reduce, "digest_for_strategy", relay)
    monkeypatch.setattr(_map_reduce, "assess_digest", assessment)
    monkeypatch.setattr(_review_identity, "build_review_identity", identity)
    monkeypatch.setattr(_review_identity, "assert_review_source_unchanged", guard)
    monkeypatch.setattr(_digest_audit, "bind_cached_audit", binder)
    entry = deep_review._review_one(
        {"item_key": "LIBR0038", "pdf_path": str(paper_io.source)}, reader=paper_io.reader,
        config=config, extractor=paper_io.extractor, llm=object(), quality_enabled=True,
        llm_digest=generator, llm_map=mapper, lean_tier=lean_tier, sub_concurrency=2,
        focus_prompt="Owned focus", interpretation_schema=C, provenance={"fixture": "owned"},
    )
    paper_io.extractor.extract_text.assert_called_once_with(str(paper_io.source))
    assert identity.call_count == relay.call_count == assessment.call_count == binder.call_count == 1
    assert identity.call_args.kwargs["interpretation_schema"] == C
    assert identity.call_args.kwargs["source_kind"] == "override"
    assert binder.call_args.kwargs == {"interpretation_schema": C}
    collector = relay.call_args.kwargs["interpretation_out"]
    assert collector is assessment.call_args.kwargs["interpretation_out"]
    assert binder.call_args.args[0]["interpretation"] is collector
    assert binder.call_args.args[0]["verification_audit"] is relay.call_args.kwargs["verification_audit"]
    assert assessment.call_args.kwargs["verification_audit"] is relay.call_args.kwargs["verification_audit"]
    cap = config.quality_review.lean_max_text_chars if lean_tier else config.quality_review.max_text_chars
    assert relay.call_args.kwargs["budget"] == _map_reduce.ChunkBudget(int(cap), config.quality_review.map_chunk_chars, 2)
    assert type(PaperDigest.model_validate(entry["digest"])) is PaperDigest
    assert "interpretation" not in entry["digest"]
    assert entry["review_identity"]["interpretation_schema"] == C and entry["review_identity"]["focus_prompt"] == "Owned focus"
    assert entry["provenance"] == {"fixture": "owned"}
    assert entry["zotero_note_written"] is False and entry["zotero_note_error"] is None
    paper_io.mirror.assert_not_called()
    paper_io.writer.assert_not_called()
    assert [call[0] for call in trace.mock_calls] == ["guard", "guard", "bind", "guard"]
    assert generator.pydantic_prompt.call_count == mapper.calls == 1
    assert mapper.map_calls == (len(_map_reduce.split_chunks(paper_io.text, config.quality_review.map_chunk_chars)) if strategy == "map_reduce" else 0)
    assert generator.pydantic_prompt.call_args.kwargs["pydantic_model"] is InterpretationDigestResponse
    audit = entry["verification_audit"]
    assert audit["identity"]["review_source_sha256"] == entry["review_identity"]["source_sha256"]
    assert audit["verified_digest"]["basis"] == "full_text"
    assert entry["digest"]["basis"] == ("map_reduce" if strategy == "map_reduce" else "full_text")
    assert entry["interpretation"] == collector == audit["verified_interpretation"]
    assert collector["native_support"]["base_digest_sha256"] == audit["identity"]["verified_digest_sha256"]
    assert audit["identity"]["cached_digest_sha256"] == _digest_audit.canonical_hash(entry["digest"])
    assert all(binding["matches_cached_finding"] and all(binding["matches_cached_basis"].values()) for binding in audit["interpretation_bindings"])
    assert paper_io.layers.call_args.args[0].text == paper_io.text
    assert not _review_cache._cache_path().exists()
    _review_cache._write_one("LIBR0038", entry)
    path = _review_cache._cache_path()
    before = path.read_bytes(), path.stat().st_mtime_ns
    view = _review_cache.get_current_review("LIBR0038", interpretation_schema=C)
    assert view["interpretation_qualification"]["status"] == "available" and view["interpretation"] == entry["interpretation"]
    assert view["verification_audit"] == audit
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce"])
@pytest.mark.parametrize("explicit_none", [False, True], ids=["omitted", "none"])
def test_default_exact_keyword_key_shape_no_c_import_and_original_native_mirror(config, paper_io, monkeypatch, strategy, explicit_none):
    config.quality_review.chunk_strategy = strategy
    mapper, generator = _Mapper("Generation notes only."), Mock()
    generator.pydantic_prompt.return_value = _base()
    relay = Mock(wraps=_map_reduce.digest_for_strategy)
    identity = Mock(wraps=_review_identity.build_review_identity)
    binder = Mock(wraps=_digest_audit.bind_cached_audit)
    guard = Mock(wraps=_review_identity.assert_review_source_unchanged)
    trace = Mock()
    trace.attach_mock(guard, "guard")
    trace.attach_mock(paper_io.mirror, "mirror")
    trace.attach_mock(binder, "bind")
    original_import = builtins.__import__

    def no_c_import(name, *args, **kwargs):
        assert name != "zotero_summarizer.models.interpretation"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_c_import)
    monkeypatch.setattr(_map_reduce, "digest_for_strategy", relay)
    monkeypatch.setattr(_review_identity, "build_review_identity", identity)
    monkeypatch.setattr(_review_identity, "assert_review_source_unchanged", guard)
    monkeypatch.setattr(_digest_audit, "bind_cached_audit", binder)
    extra = {"interpretation_schema": None} if explicit_none else {}
    entry = deep_review._review_one(
        {"item_key": "LIBR0038", "pdf_path": str(paper_io.source)}, reader=paper_io.reader,
        config=config, extractor=paper_io.extractor, llm=generator, llm_map=mapper, quality_enabled=True, **extra,
    )
    assert identity.call_args.kwargs == {"config": config, "pdf_path": str(paper_io.source), "source_kind": "override", "focus_prompt": ""}
    assert binder.call_args.kwargs == {}
    assert set(relay.call_args.kwargs) == {"map_llm", "reduce_llm", "budget", "focus_prompt", "response_format", "verification_audit"}
    assert "interpretation_schema" not in entry["review_identity"]
    assert set(entry) == {"review_contract_version", "review_identity", "provenance", "digest", "verification_audit",
                          "quality", "goal_summaries", "paper_type", "section_overlay", "code_link", "model_read_decision",
                          "reading_policy_flags", "needs_pdf", "needs_login", "login_url", "gate_relevance", "reviewed_at",
                          "zotero_note_written", "zotero_note_error"}
    assert generator.pydantic_prompt.call_args.kwargs["pydantic_model"] is PaperDigest
    assert "interpretation_evidence" not in entry["verification_audit"]
    assert [call[0] for call in trace.mock_calls] == ["guard", "guard", "bind"]
    paper_io.mirror.assert_not_called()  # proposal staging belongs after publication
    paper_io.writer.assert_not_called()
    assert entry["zotero_note_written"] is False and entry["zotero_note_error"] is None
    assert generator.pydantic_prompt.call_count == mapper.calls == 1


@pytest.mark.parametrize("selection", ["unknown", "", "paper-digest-v1", False, 0])
def test_unknown_condition_refuses_before_item_reader_model_identity_or_native_io(monkeypatch, selection):
    no_io = Mock(side_effect=AssertionError("condition must refuse before IO"))
    monkeypatch.setattr(_review_identity, "build_review_identity", no_io)
    monkeypatch.setattr(_map_reduce, "digest_for_strategy", no_io)
    monkeypatch.setattr(_digest_audit, "bind_cached_audit", no_io)
    monkeypatch.setattr(deep_review, "_mirror_digest_note", no_io)
    with pytest.raises(ValueError, match="review_condition_mismatch"):
        deep_review._review_one({}, reader=no_io, config=no_io, extractor=no_io, llm=no_io,
                                quality_enabled=True, interpretation_schema=selection)
    no_io.assert_not_called()
    assert no_io.mock_calls == []


@pytest.mark.parametrize("case", ["missing_item", "missing_pdf", "disabled", "empty_extraction"])
def test_c_without_a_generated_digest_never_forges_empty_optional_content(config, paper_io, monkeypatch, case):
    if case == "missing_item":
        paper_io.reader.get_item_detail.return_value = None
    elif case == "missing_pdf":
        paper_io.reader.get_item_detail.return_value["pdf_path"] = ""
    elif case == "empty_extraction":
        paper_io.extractor.extract_text.return_value = " \n\t"
    relay = Mock(wraps=_map_reduce.digest_for_strategy)
    binder = Mock(wraps=_digest_audit.bind_cached_audit)
    generator = Mock()
    monkeypatch.setattr(_map_reduce, "digest_for_strategy", relay)
    monkeypatch.setattr(_digest_audit, "bind_cached_audit", binder)
    item = {"item_key": "LIBR0038"}
    if case not in {"missing_item", "missing_pdf"}:
        item["pdf_path"] = str(paper_io.source)
    kwargs = {"reader": paper_io.reader, "config": config, "extractor": paper_io.extractor,
              "llm": generator, "quality_enabled": case != "disabled", "interpretation_schema": C}
    if case == "empty_extraction":
        with pytest.raises(ValueError, match="PDF extraction returned no text"):
            deep_review._review_one(item, **kwargs)
        binder.assert_not_called()
    else:
        entry = deep_review._review_one(item, **kwargs)
        if case == "missing_item":
            assert entry is None
            binder.assert_not_called()
        else:
            assert entry["digest"] is None and entry["verification_audit"] is None
            assert "interpretation" not in entry and "interpretation_qualification" not in entry
            assert entry["needs_pdf"] is (case == "missing_pdf")
            assert entry["zotero_note_written"] is False and entry["zotero_note_error"] is None
    relay.assert_not_called()
    generator.pydantic_prompt.assert_not_called()
    paper_io.layers.assert_not_called()
    paper_io.mirror.assert_not_called()
    paper_io.writer.assert_not_called()


@pytest.mark.parametrize("strategy", ["rank", "prefix", "map_reduce"])
@pytest.mark.parametrize("optional", ["missing", "malformed", "numeric", "semantic"])
def test_native_optional_unavailable_is_bound_truthfully_and_only_valid_base_survives_cache_read(config, paper_io, strategy, optional):
    config.quality_review.chunk_strategy = strategy
    card = None if optional == "missing" else ({"items": []} if optional == "malformed" else _card("Unreported 99999%.") if optional == "numeric" else _card())
    generator = Mock()
    generator.pydantic_prompt.return_value = _payload(card)
    mapper = _Mapper("Generation context only.") if optional != "semantic" else Mock()
    if optional == "semantic":
        mapper.prompt.return_value = "Generation context only."
        count = len(_claims(_base()))
        mapper.pydantic_prompt.return_value = {"checks": _table(count + 5, rejected=(count + 1,))}
    entry = deep_review._review_one(
        {"item_key": "LIBR0038", "pdf_path": str(paper_io.source)}, reader=paper_io.reader, config=config,
        extractor=paper_io.extractor, llm=generator, llm_map=mapper, quality_enabled=True, interpretation_schema=C,
    )
    payload = entry["interpretation"]
    assert payload["status"] == "unavailable" and payload["scientific_verification"] == "unverified"
    assert "items" not in payload and "native_support" not in payload
    assert entry["verification_audit"]["identity"]["interpretation_sha256"] == _digest_audit.canonical_hash(payload)
    assert _digest_audit.cached_audit_status(entry, interpretation_schema=C) == ("available", "")
    assert generator.pydantic_prompt.call_count == 1
    paper_io.mirror.assert_not_called()
    paper_io.writer.assert_not_called()
    _review_cache._write_one("LIBR0038", entry)
    before = _review_cache._cache_path().read_bytes()
    view = _review_cache.get_current_review("LIBR0038", interpretation_schema=C)
    _assert_base_only(view)
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize("selection", [None, C], ids=["off", "c"])
@pytest.mark.parametrize("stage", ["extract", "generation", "layers"])
def test_known_source_mutation_refuses_before_native_mirror_or_final_binding(config, paper_io, monkeypatch, selection, stage):
    generator, mapper = Mock(), _Mapper("Generation notes only.")
    reply = _payload(_card()) if selection is not None else _base()

    def replace_source(*args, **kwargs):
        paper_io.source.write_bytes(b"R" * paper_io.source.stat().st_size)
        return paper_io.text if stage == "extract" else (reply if stage == "generation" else (None, None, None, None, None))

    generator.pydantic_prompt.side_effect = replace_source if stage == "generation" else None
    generator.pydantic_prompt.return_value = reply
    if stage == "extract":
        paper_io.extractor.extract_text.side_effect = replace_source
    elif stage == "layers":
        paper_io.layers.side_effect = replace_source
    binder = Mock(wraps=_digest_audit.bind_cached_audit)
    monkeypatch.setattr(_digest_audit, "bind_cached_audit", binder)
    with pytest.raises(ValueError, match="source changed"):
        deep_review._review_one(
            {"item_key": "LIBR0038", "pdf_path": str(paper_io.source)}, reader=paper_io.reader, config=config,
            extractor=paper_io.extractor, llm=generator, llm_map=mapper, quality_enabled=True, interpretation_schema=selection,
        )
    assert generator.pydantic_prompt.call_count == mapper.calls == 1
    binder.assert_not_called()
    paper_io.mirror.assert_not_called()
    paper_io.writer.assert_not_called()
    assert not _review_cache._cache_path().exists()


@pytest.mark.parametrize("failure", ["base_rejected", "verifier_unavailable", "generator_exception"])
def test_native_failure_returns_no_forged_entry_and_does_not_bind_or_write(config, paper_io, monkeypatch, failure):
    generator = Mock()
    mapper = _Mapper("Generation context only.")
    error = DigestSourceRejected
    generator.pydantic_prompt.return_value = _payload(_card())
    if failure == "base_rejected":
        mapper.supported = False
    elif failure == "verifier_unavailable":
        mapper = Mock()
        mapper.pydantic_prompt.return_value = ""
        generator.pydantic_prompt.side_effect = [_payload(_card()), "", ""]
        error = DigestVerifierUnavailable
    else:
        generator.pydantic_prompt.side_effect = RuntimeError("scripted generator failure")
        error = RuntimeError
    relay = Mock(wraps=_map_reduce.digest_for_strategy)
    binder = Mock(wraps=_digest_audit.bind_cached_audit)
    monkeypatch.setattr(_map_reduce, "digest_for_strategy", relay)
    monkeypatch.setattr(_digest_audit, "bind_cached_audit", binder)
    with pytest.raises(error):
        deep_review._review_one(
            {"item_key": "LIBR0038", "pdf_path": str(paper_io.source)}, reader=paper_io.reader, config=config,
            extractor=paper_io.extractor, llm=generator, llm_map=mapper, quality_enabled=True, interpretation_schema=C,
        )
    assert relay.call_args.kwargs["interpretation_out"] == {}
    binder.assert_not_called()
    paper_io.mirror.assert_not_called()
    paper_io.writer.assert_not_called()
    assert not _review_cache._cache_path().exists()


def test_each_paper_gets_a_distinct_collector_and_missing_next_card_never_reuses_first(config, paper_io, monkeypatch):
    generator, mapper = Mock(), _Mapper("Generation notes only.")
    generator.pydantic_prompt.side_effect = [_payload(_card()), _payload(None), RuntimeError("third paper failed")]
    relay = Mock(wraps=_map_reduce.digest_for_strategy)
    monkeypatch.setattr(_map_reduce, "digest_for_strategy", relay)
    entries = []
    for key in ("LIBR0038", "LIBR0039"):
        entries.append(deep_review._review_one(
            {"item_key": key, "pdf_path": str(paper_io.source)}, reader=paper_io.reader, config=config,
            extractor=paper_io.extractor, llm=generator, llm_map=mapper, quality_enabled=True, interpretation_schema=C,
        ))
    with pytest.raises(RuntimeError, match="third paper failed"):
        deep_review._review_one(
            {"item_key": "LIBR0040", "pdf_path": str(paper_io.source)}, reader=paper_io.reader, config=config,
            extractor=paper_io.extractor, llm=generator, llm_map=mapper, quality_enabled=True, interpretation_schema=C,
        )
    first, second, third = [call.kwargs["interpretation_out"] for call in relay.call_args_list]
    assert first is not second and first is not third and second is not third
    assert entries[0]["interpretation"]["items"] == first["items"] == _card()["items"]
    assert entries[1]["interpretation"]["unavailable_reason"] == "missing_interpretation"
    assert "items" not in second and third == {}
    paper_io.mirror.assert_not_called()
    paper_io.writer.assert_not_called()
