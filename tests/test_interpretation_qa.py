"""Private QA machine contracts, NOT RUN by the source-only owner.

Owned fixtures/Mock only; fixed parsed outputs are not language-meaning grades.
Injected adapter tests isolate guards, not allocator reachability or native IO.
"""
import builtins
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_library_qa import _Extractor, _LLM, _fake_state
from test_qa_history_prompt_boundary import _expected_prompt, _history_request
from tests.test_interpretation_binding import C, _native_entry, config
from tests.test_interpretation_producer import _card
from tests.test_interpretation_review_one import paper_io
from tests.test_review_cache import _isolate_cache
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.api.routes.library import AskPaperRequest
from zotero_summarizer.services.library import (
    _digest_audit, _interpretation_qa as iq, _interpretation_view as iv,
    _paper_read_meta as meta, _review_cache, deep_review, qa, qa_context,
)
from zotero_summarizer.services.library.review_fleet import propose

KEY = "LIBR0038"
QUESTION = "Which dataset was used?"
QUOTE = "We evaluated GlassNet on the ImageNet dataset."
LATE = "We evaluated the invented benchmark SecretSet with perfect accuracy."
HEADER = "Title: Owned\n\nPages: 0\n\nFigures: 0\n\nReferences: 0"
LEGACY_ARTIFACT_TEXT = qa.paper_render.artifact_text
AVAILABLE = {"availability": "available", "reason": ""}
BUDGET = {"availability": "unavailable", "reason": "context_budget"}


def _wire(fields):
    return "Structured review:\n" + json.dumps(fields, ensure_ascii=False, default=str)


@pytest.fixture
def snapshot():
    # Pure placement input, NOT a forged native qualification fixture.
    basis = ["/parameters", "/parameters/external_validation", "/methods", "/methods"]
    return {
        "digest": {"tldr": "A", "key_findings": ["A", "B", "A"], "methods": "M",
                   "parameters": {"dataset": None, "sample_size": 0, "external_validation": False}},
        "quality": {"red_flags": ["A"]}, "goal_summaries": [{"summary": "A"}],
        "interpretation_view": {
            **AVAILABLE, "origin": "ai_generated", "scientific_verification": "unverified",
            "contradiction_adjudication": "not_performed", "evidence_state": "bound_snapshot",
            "groups": {
                "Finding": [{"finding_ref": "/key_findings/2", "inference_strength": "I", "rationale": "R"}],
                "Scope": [{"finding_ref": "/key_findings/2", "scope": "S", "basis_refs": basis}],
                "Unproven": [{"finding_ref": "/key_findings/2", "uncertainties": ["U", "U"],
                              "unsupported_extrapolations": [], "basis_refs": list(basis)}],
            },
            "passage_support": [{"claim_index": 9, "scientific_ref": "key_findings[2]", "part": 1,
                                 **AVAILABLE, "passage_refs": [7, 7]}],
        },
    }


def _expected_candidate(review, body):
    retained = {key: review[key] for key in ("quality", "goal_summaries") if review.get(key)}
    parts = [HEADER]
    if retained:
        parts.append(_wire(retained))
    if body:
        parts.append(body)
    parts.append(_wire({"digest": review["digest"], "interpretation_view": review["interpretation_view"]}))
    return "\n\n".join(parts)


def _qa_state(tmp_path, monkeypatch, body=QUOTE, cap=60_000):
    llm = _LLM()
    llm.prompt = Mock(return_value=json.dumps({"answer": "ImageNet", "quote": QUOTE}))
    app = _fake_state(tmp_path, tmp_path / "unused.pdf", _Extractor(), llm, monkeypatch)
    app.app_state.config.quality_review.max_text_chars = cap
    artifact = {"item_key": KEY, "pdf_key": "qa-v1", "title": "Owned", "full_text": body}
    monkeypatch.setattr(qa.paper_render, "build_paper_read", Mock(return_value=artifact))
    monkeypatch.setattr(qa.paper_render, "artifact_text", Mock(side_effect=AssertionError("No default C lookup")))
    return app, llm, artifact


def _no_optional_imports(monkeypatch):
    original = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        assert name not in {"zotero_summarizer.services.library._interpretation_qa",
                            "zotero_summarizer.services.library._interpretation_view"}
        assert not {"_interpretation_qa", "_interpretation_view"}.intersection(fromlist or ())
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)


@pytest.mark.parametrize("delta", [0, 1, -1], ids=["exact", "free-space", "one-char-overflow"])
def test_whole_bundle_exact_boundary_and_input_isolation(snapshot, delta):
    body = " \né🙂Z \n"
    artifact = {"title": "Owned", "full_text": body}
    expected = _expected_candidate(snapshot, body)
    before = deepcopy(snapshot)
    legacy_expected = HEADER + "\n\n" + _wire({key: snapshot[key] for key in
                                              ("digest", "quality", "goal_summaries")}) + "\n\n" + body
    legacy, actual, status = iq.allocate_context(artifact, max_chars=len(expected) + delta, review=snapshot)
    assert legacy == legacy_expected
    assert (actual, status) == ((expected, AVAILABLE) if delta >= 0 else (legacy_expected, BUDGET))
    assert snapshot == before
    if delta >= 0:
        start = HEADER + "\n\n" + _wire({key: snapshot[key] for key in ("quality", "goal_summaries")})
        start += "\n\n" + body + "\n\nStructured review:\n"
        bundle = json.loads(actual[len(start):])
        assert bundle == {"digest": before["digest"], "interpretation_view": before["interpretation_view"]}
        assert bundle["digest"]["parameters"]["external_validation"] is False
        assert type(bundle["digest"]["parameters"]["sample_size"]) is int
        assert bundle["digest"]["key_findings"] == ["A", "B", "A"]
        assert actual.count('"digest":') == actual.count('"interpretation_view":') == 1
        bundle["interpretation_view"]["groups"]["Scope"][0]["basis_refs"].clear()
        status["reason"] = "output-only"
        assert snapshot == before


def test_structural_origin_with_repeated_body_in_title_and_digest(snapshot, monkeypatch):
    assembler = Mock(wraps=meta.assemble_qa_text)
    monkeypatch.setattr(meta, "assemble_qa_text", assembler)
    body = " \né🙂Z \n"
    artifact = {"title": body, "qa_text": body, "full_text": "WRONG"}
    snapshot["digest"]["tldr"] = body
    prefix = "Title: " + body + "\n\nPages: 0\n\nFigures: 0\n\nReferences: 0\n\n"
    prefix += _wire({key: snapshot[key] for key in ("digest", "quality", "goal_summaries")}) + "\n\n"
    legacy, actual, status = iq.allocate_context(artifact, max_chars=len(prefix) + 4, review=snapshot)
    assert legacy == actual == prefix + " \né🙂"
    assert status == BUDGET
    # Candidate necessarily overflows; observe structural P at the shared assembly seam.
    candidates = [call for call in assembler.call_args_list if "after_body" in call.kwargs]
    assert len(candidates) == 1 and candidates[0].args[1] == " \né🙂"
    assert meta.artifact_text(artifact, max_chars=len(prefix) + 4, review=snapshot) == legacy


@pytest.mark.parametrize("body_fields,body", [
    ({"full_text": "fallback"}, "fallback"),
    ({"qa_text": None, "full_text": "WRONG"}, ""),
    ({"qa_text": "", "full_text": "WRONG"}, ""),
    ({"qa_text": " \t\n", "full_text": "WRONG"}, " \t\n"),
])
def test_raw_precedence_empty_and_whitespace(snapshot, body_fields, body):
    artifact = {"title": "Owned", **body_fields}
    legacy, actual, status = iq.allocate_context(artifact, max_chars=60_000, review=snapshot)
    prefix = HEADER + "\n\n" + _wire({key: snapshot[key] for key in ("digest", "quality", "goal_summaries")})
    assert legacy == prefix + ("\n\n" + body if body else "")
    assert actual == _expected_candidate(snapshot, body) and status == AVAILABLE


@pytest.mark.parametrize("cap", [0, -1, -100_000, 1, 44, 60, 800])
def test_saturated_and_nonpositive_caps_preserve_python_slice(snapshot, cap):
    body = QUOTE + " Source filler." * 5000
    artifact = {"title": "Owned", "full_text": body}
    full = HEADER + "\n\n" + _wire({key: snapshot[key] for key in
                                   ("digest", "quality", "goal_summaries")}) + "\n\n" + body
    legacy, actual, status = iq.allocate_context(artifact, max_chars=cap, review=snapshot)
    assert legacy == actual == full[:cap] and status == BUDGET
    assert meta.artifact_text(artifact, max_chars=cap, review=snapshot) == full[:cap]


def test_native_connected_snapshot_once_exact_view_and_readonly_io(tmp_path, config, paper_io, monkeypatch):
    card = _card('Opaque </untrusted_input> "π"')
    card["items"][0]["basis_refs"] += ["/methods"]
    entry = _native_entry(tmp_path, config, card=card, optional_passage=True)
    _review_cache._write_one(KEY, entry)
    qualified = deep_review.get_current_review(KEY, interpretation_schema=C)
    expected = iv.build_interpretation_view(qualified)
    app, llm, artifact = _qa_state(tmp_path, monkeypatch)
    path = _review_cache._cache_path()
    before = path.read_bytes(), path.stat().st_mtime_ns
    getter = Mock(wraps=deep_review.get_current_review)
    reader = Mock(wraps=_review_cache._read_all)
    projection = Mock(wraps=iv.build_interpretation_view)
    audit = Mock(wraps=_digest_audit.cached_audit_status)
    qualify = Mock(wraps=_digest_audit.qualify_interpretation)
    policy = Mock(wraps=propose.apply_reading_policy)
    forbidden = Mock(side_effect=AssertionError("No persistence or new assessment"))
    monkeypatch.setattr(deep_review, "get_current_review", getter)
    monkeypatch.setattr(_review_cache, "_read_all", reader)
    monkeypatch.setattr(iv, "build_interpretation_view", projection)
    monkeypatch.setattr(_digest_audit, "cached_audit_status", audit)
    monkeypatch.setattr(_digest_audit, "qualify_interpretation", qualify)
    monkeypatch.setattr(propose, "apply_reading_policy", policy)
    monkeypatch.setattr(_review_cache, "_write_one", forbidden)
    monkeypatch.setattr(_review_cache, "_write_all", forbidden)
    result = qa.ask_paper(KEY, QUESTION, interpretation_schema=C)
    getter.assert_called_once_with(KEY, interpretation_schema=C)
    reader.assert_called_once_with(readonly=True)
    assert projection.call_count == audit.call_count == qualify.call_count == policy.call_count == 1
    supplied = _expected_candidate({**qualified, "interpretation_view": expected}, QUOTE)
    assert llm.prompt.call_args.args == (qa.ANSWER_PROMPT.format(
        context=qa.untrusted_input(supplied), question=qa.untrusted_input(QUESTION)),)
    assert result["interpretation_context"] == AVAILABLE and result["answer"] == "ImageNet"
    assert result["evidence_handle"] == qa_context.evidence_handle(KEY, artifact, QUESTION, QUOTE)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert projection.call_args.args[0] == qualified
    forbidden.assert_not_called()
    paper_io.mirror.assert_not_called()
    paper_io.writer.assert_not_called()
    qa.paper_render.artifact_text.assert_not_called()
    assert llm.prompt.call_count == 1


@pytest.mark.parametrize("case", ["missing_optional", "tampered_optional", "absent", "stale", "source_missing"])
def test_connected_native_unavailable_uses_same_qualified_base_or_none(tmp_path, config, monkeypatch, case):
    if case != "absent":
        entry = _native_entry(tmp_path, config, missing=case == "missing_optional")
        if case == "tampered_optional":
            entry["interpretation"]["items"][0]["rationale"] += " "
        if case == "stale":
            entry["digest"]["tldr"] += "!"
        if case == "source_missing":
            Path(entry["review_identity"]["source_path"]).unlink()
        _review_cache._write_one(KEY, entry)
    expected = iv.current_review_view(KEY, interpretation_schema=C)
    app, llm, artifact = _qa_state(tmp_path, monkeypatch)
    getter = Mock(wraps=deep_review.get_current_review)
    monkeypatch.setattr(deep_review, "get_current_review", getter)
    result = qa.ask_paper(KEY, QUESTION, interpretation_schema=C)
    getter.assert_called_once_with(KEY, interpretation_schema=C)
    reason = expected["interpretation_view"]["reason"] if expected else "review_unavailable"
    assert result["interpretation_context"] == {"availability": "unavailable", "reason": reason}
    context = meta.artifact_text(artifact, max_chars=60_000, review=expected)
    assert llm.prompt.call_args.args == (qa.ANSWER_PROMPT.format(
        context=qa.untrusted_input(context), question=qa.untrusted_input(QUESTION)),)
    if expected:
        assert "interpretation" not in expected and "verification_audit" not in expected
    assert llm.prompt.call_count == 1


@pytest.mark.parametrize("case", ["corrupt", "getter_error", "malformed", "unknown"])
def test_used_c_errors_do_not_forge_success_or_call_model(tmp_path, monkeypatch, case):
    app, llm, artifact = _qa_state(tmp_path, monkeypatch)
    path = _review_cache._cache_path()
    if case == "corrupt":
        path.write_bytes(b'{"reviews":')
        before = path.stat().st_mtime_ns
    getter = Mock(side_effect=RuntimeError("owned getter error")) if case == "getter_error" else Mock(return_value={})
    if case in {"getter_error", "malformed", "unknown"}:
        monkeypatch.setattr(deep_review, "get_current_review", getter)
    error = RuntimeError if case == "getter_error" else KeyError if case == "malformed" else ValueError
    with pytest.raises(error):
        qa.ask_paper(KEY, QUESTION, interpretation_schema="unknown" if case == "unknown" else C)
    llm.prompt.assert_not_called()
    qa.paper_render.artifact_text.assert_not_called()
    if case == "corrupt":
        assert path.read_bytes() == b'{"reviews":' and path.stat().st_mtime_ns == before
        assert list(tmp_path.iterdir()) == [path]
    elif case == "unknown":
        getter.assert_not_called()
    else:
        getter.assert_called_once_with(KEY, interpretation_schema=C)


@pytest.mark.parametrize("explicit_none", [False, True])
@pytest.mark.parametrize("stored_c", [False, True])
def test_off_exact_calls_shape_no_optional_work(tmp_path, config, monkeypatch, explicit_none, stored_c):
    app, llm, artifact = _qa_state(tmp_path, monkeypatch)
    if stored_c:
        _review_cache._write_one(KEY, _native_entry(tmp_path, config))
    getter = Mock(wraps=deep_review.get_current_review) if stored_c else Mock(return_value=None)
    context = Mock(wraps=LEGACY_ARTIFACT_TEXT)
    monkeypatch.setattr(deep_review, "get_current_review", getter)
    monkeypatch.setattr(qa.paper_render, "artifact_text", context)
    monkeypatch.setattr(qa, "perf_counter", lambda: 1.0)
    no_view = Mock(side_effect=AssertionError("OFF has no optional work"))
    monkeypatch.setattr(iv, "current_review_view", no_view)
    monkeypatch.setattr(iv, "build_interpretation_view", no_view)
    state = Mock(return_value=app)
    routing = Mock(wraps=qa.resolve_stage)
    history = Mock(wraps=qa_context.compact_history)
    client = Mock(return_value=llm)
    evidence = Mock(wraps=qa._with_evidence)
    monkeypatch.setattr(qa, "state", state)
    monkeypatch.setattr(qa, "resolve_stage", routing)
    monkeypatch.setattr(qa_context, "compact_history", history)
    monkeypatch.setattr(app, "resolve_stage_client", client)
    monkeypatch.setattr(qa, "_with_evidence", evidence)
    trace = Mock()
    for name, fn in (("build", qa.paper_render.build_paper_read), ("state", state), ("routing", routing),
                     ("context", context), ("getter", getter), ("history", history), ("client", client),
                     ("model", llm.prompt), ("evidence", evidence)):
        trace.attach_mock(fn, name)
    _no_optional_imports(monkeypatch)
    extra = {"interpretation_schema": None} if explicit_none else {}
    result = qa.ask_paper(KEY, QUESTION, **extra)
    assert [call[0] for call in trace.mock_calls] == [
        "build", "state", "routing", "context", "getter", "history", "client", "model", "evidence"]
    context.assert_called_once_with(artifact, max_chars=60_000)
    getter.assert_called_once_with(KEY)
    assert result == {
        "item_key": KEY, "question": QUESTION, "answer": "ImageNet", "abstained": False, "quote": QUOTE,
        "mode": "comprehensive", "chunks_used": 0, "latency_seconds": 0.0, "model": "local-35b",
        "history_compacted": False, "history_evidence_handles": 0,
        "evidence_handle": qa_context.evidence_handle(KEY, artifact, QUESTION, QUOTE),
        "citation": {"claimed": True, "quote_verified": True, "location_verified": True,
                     "evidence_handle": qa_context.evidence_handle(KEY, artifact, QUESTION, QUOTE)},
    }
    assert "interpretation_schema" not in AskPaperRequest.model_fields
    assert qa.ask_paper.__kwdefaults__["interpretation_schema"] is None
    no_view.assert_not_called()


@pytest.mark.parametrize("mode,question", [("retrieval", QUESTION), ("full_text", QUESTION),
                                           ("comprehensive", "How many pages are in the paper?")])
@pytest.mark.parametrize("selection", [C, "unknown"])
def test_unused_private_keyword_does_no_c_work(tmp_path, monkeypatch, mode, question, selection):
    app, llm, artifact = _qa_state(tmp_path, monkeypatch)
    forbidden = Mock(side_effect=AssertionError("No unused C lookup"))
    monkeypatch.setattr(iv, "current_review_view", forbidden)
    monkeypatch.setattr(deep_review, "get_current_review", forbidden)
    _no_optional_imports(monkeypatch)
    result = qa.ask_paper(KEY, question, mode=mode, interpretation_schema=selection)
    assert "interpretation_context" not in result
    assert llm.prompt.call_count == (0 if mode == "comprehensive" else 1)
    assert result["mode"] == ("metadata" if mode == "comprehensive" else mode)
    forbidden.assert_not_called()
    qa.paper_render.artifact_text.assert_not_called()


@pytest.mark.parametrize("mode", ["retrieval", "full_text"])
def test_other_mode_empty_extraction_still_raises(tmp_path, monkeypatch, mode):
    app, llm, artifact = _qa_state(tmp_path, monkeypatch, body=" \t\n")
    with pytest.raises(APIError) as caught:
        qa.ask_paper(KEY, QUESTION, mode=mode, interpretation_schema="unknown")
    assert caught.value.error == "extraction_empty" and caught.value.status_code == 422
    llm.prompt.assert_not_called()


@pytest.mark.parametrize("answer", ["ImageNet", "unverified"])
@pytest.mark.parametrize("delta", [0, 1, -1])
def test_connected_boundary_authority_firewall(tmp_path, monkeypatch, snapshot, answer, delta):
    expected = _expected_candidate(snapshot, QUOTE)
    app, llm, artifact = _qa_state(tmp_path, monkeypatch, cap=len(expected) + delta)
    getter = Mock(return_value=snapshot)
    monkeypatch.setattr(iv, "current_review_view", getter)
    llm.prompt.return_value = json.dumps({"answer": answer, "quote": QUOTE})
    result = qa.ask_paper(KEY, QUESTION, interpretation_schema=C)
    getter.assert_called_once_with(KEY, interpretation_schema=C)
    assert result["interpretation_context"] == (BUDGET if delta < 0 else AVAILABLE)
    supplied = (HEADER + "\n\n" + _wire({key: snapshot[key] for key in
                ("digest", "quality", "goal_summaries")}) + "\n\n" + QUOTE) if delta < 0 else expected
    assert llm.prompt.call_args.args == (qa.ANSWER_PROMPT.format(
        context=qa.untrusted_input(supplied), question=qa.untrusted_input(QUESTION)),)
    assert result["answer"] == ("ImageNet" if answer == "ImageNet" else None)
    assert result["abstained"] is (answer != "ImageNet")
    assert llm.prompt.call_count == 1


@pytest.mark.parametrize("in_original", [False, True])
def test_connected_overflow_retains_legacy_late_quote_policy(tmp_path, monkeypatch, snapshot, in_original):
    snapshot["digest"]["key_findings"] = [LATE]
    body = QUOTE + " Original filler." * 5000 + (LATE if in_original else "")
    prefix = HEADER + "\n\n" + _wire({key: snapshot[key] for key in ("digest", "quality", "goal_summaries")})
    app, llm, artifact = _qa_state(tmp_path, monkeypatch, body=body, cap=len(prefix) + 2 + len(QUOTE))
    monkeypatch.setattr(iv, "current_review_view", Mock(return_value=snapshot))
    llm.prompt.return_value = json.dumps({"answer": "SecretSet", "quote": LATE})
    result = qa.ask_paper(KEY, QUESTION, interpretation_schema=C)
    expected = prefix + "\n\n" + QUOTE  # Independently admitted P is precisely QUOTE.
    assert llm.prompt.call_args.args == (qa.ANSWER_PROMPT.format(
        context=qa.untrusted_input(expected), question=qa.untrusted_input(QUESTION)),)
    assert result["interpretation_context"] == BUDGET
    assert result["answer"] == ("SecretSet" if in_original else None)
    assert result["citation"]["quote_verified"] is in_original
    assert llm.prompt.call_count == 1


@pytest.mark.parametrize("handoff", [
    (QUOTE, QUOTE + " " + LATE, "SecretSet", LATE, False),
    (QUOTE + " hiddenlegacytoken", QUOTE, "hiddenlegacytoken", QUOTE, False),
    (QUOTE + " " + LATE, QUOTE + " " + LATE + " unverified", "SecretSet", LATE, True),
])
def test_injected_adapter_orthogonal_guard_composition(tmp_path, monkeypatch, handoff):
    legacy, actual, answer, quote, accepted = handoff
    # NOT reachable full-fit allocations: whole-unit saturated contexts overflow.
    # Literal adapter I/O isolates L authority AND A visibility in actual ask_paper.
    app, llm, artifact = _qa_state(tmp_path, monkeypatch, body=QUOTE + " filler " * 100 + LATE)
    adapter = Mock(return_value=(legacy, actual, dict(AVAILABLE)))
    monkeypatch.setattr(iq, "question_context", adapter)
    llm.prompt.return_value = json.dumps({"answer": answer, "quote": quote})
    result = qa.ask_paper(KEY, QUESTION, interpretation_schema=C)
    adapter.assert_called_once_with(KEY, artifact, max_chars=60_000, interpretation_schema=C)
    assert result["answer"] == (answer if accepted else None)
    assert result["abstained"] is (not accepted)
    assert llm.prompt.call_count == 1


@pytest.mark.parametrize("responses,abstained", [
    ((json.dumps({"answer": "ImageNet", "quote": QUOTE}),), False),
    (('{"answer": null, "quote": null}',), True),
    (("not JSON", json.dumps({"answer": "ImageNet", "quote": QUOTE})), False),
    (("not JSON", "still not JSON"), None),
])
@pytest.mark.parametrize("history_kind", [0, 4, "compacted"])
def test_connected_history_escaping_and_real_attempt_budget(tmp_path, monkeypatch, snapshot, responses, abstained, history_kind):
    from test_library_qa import PAPER_TEXT
    from test_qa_history_prompt_boundary import _UNSAFE

    body = PAPER_TEXT + "\n" + _UNSAFE
    app, llm, artifact = _qa_state(tmp_path, monkeypatch, body=body)
    monkeypatch.setattr(iv, "current_review_view", Mock(return_value=snapshot))
    request, history = _history_request(artifact, history_kind)
    prior, compacted, count = qa_context.compact_history(request.item_key, artifact, history)
    expected = _expected_prompt(_expected_candidate(snapshot, body), prior)
    llm.prompt.side_effect = responses
    if abstained is None:
        with pytest.raises(ValueError):
            qa.ask_paper(request.item_key, request.question, history=history, interpretation_schema=C)
    else:
        result = qa.ask_paper(request.item_key, request.question, history=history, interpretation_schema=C)
        assert result["abstained"] is abstained and result["interpretation_context"] == AVAILABLE
        assert (result["history_compacted"], result["history_evidence_handles"]) == (compacted, count)
    prompts = [call.args[0] for call in llm.prompt.call_args_list]
    assert prompts == [expected] + ([
        'Extract ONE valid JSON object {"answer": ..., "quote": ...} from the '
        "following text and return ONLY it:\n\n" + responses[0]] if len(responses) == 2 else [])


@pytest.mark.parametrize("selection", [None, C])
def test_guard_primitive_arguments_and_short_circuit_order(tmp_path, monkeypatch, selection):
    app, llm, artifact = _qa_state(tmp_path, monkeypatch)
    legacy = "old-authority"
    actual = "shown-context"
    adapter = Mock(return_value=(legacy, actual, dict(AVAILABLE)))
    monkeypatch.setattr(iq, "question_context", adapter)
    monkeypatch.setattr(qa.paper_render, "artifact_text", Mock(return_value=legacy))
    quotes = Mock(side_effect=[True, True] + ([True] if selection else []))
    answers = Mock(side_effect=[False, True] + ([False, True] if selection else []))
    monkeypatch.setattr(qa, "_quote_is_grounded", quotes)
    monkeypatch.setattr(qa, "_answer_is_supported", answers)
    trace = Mock()
    trace.attach_mock(quotes, "quote")
    trace.attach_mock(answers, "answer")
    result = qa.ask_paper(KEY, QUESTION, interpretation_schema=selection)
    expected = [("quote", (QUOTE, legacy)), ("quote", (QUOTE, QUOTE)),
                ("answer", ("ImageNet", QUOTE)), ("answer", ("ImageNet", legacy))]
    if selection:
        expected += [("quote", (QUOTE, actual)), ("answer", ("ImageNet", QUOTE)),
                     ("answer", ("ImageNet", actual))]
    assert [(call[0], call.args) for call in trace.mock_calls] == expected
    assert result["answer"] == "ImageNet" and llm.prompt.call_count == 1
    assert adapter.call_count == (1 if selection else 0)


def test_legacy_default_str_and_serialization_before_body_are_preserved(monkeypatch):
    marker = Mock()
    marker.__str__ = Mock(return_value="π")
    body = Mock(return_value="raw")
    monkeypatch.setattr(meta, "qa_body_text", body)
    trace = Mock()
    trace.attach_mock(marker.__str__, "serialize")
    trace.attach_mock(body, "body")
    actual = meta.artifact_text({"title": "Owned"}, max_chars=60_000, review={"digest": {"marker": marker}})
    assert actual == HEADER + '\n\nStructured review:\n{"digest": {"marker": "π"}}\n\nraw'
    assert [call[0] for call in trace.mock_calls] == ["serialize", "body"]


def test_connected_c_adds_no_body_admission_calls(tmp_path, monkeypatch, snapshot):
    app, llm, artifact = _qa_state(tmp_path, monkeypatch)
    admission = Mock()
    monkeypatch.setattr("zotero_summarizer.services.library._source_admission.admit_source", admission)
    monkeypatch.setattr(qa.paper_render, "artifact_text", LEGACY_ARTIFACT_TEXT)
    monkeypatch.setattr(deep_review, "get_current_review", Mock(return_value=None))
    qa.ask_paper(KEY, QUESTION)
    off_calls = admission.call_args_list[:]
    admission.reset_mock()
    monkeypatch.setattr(iv, "current_review_view", Mock(return_value=snapshot))
    qa.ask_paper(KEY, QUESTION, interpretation_schema=C)
    assert admission.call_args_list == off_calls and off_calls
