"""Real triage wiring with scripted replies, not scientific/model evaluation."""
import hashlib
import html
import json
import re
from threading import Event
from unittest.mock import Mock

import pytest

from test_method_source_admission import config, receipt_path, rows, wire_pipeline
from zotero_summarizer.models import RefinedSummary, SummarizeRequest
from zotero_summarizer.services.library import _map_reduce
from zotero_summarizer.services.triage import summarization
from zotero_summarizer.services.triage._execution import JobStopped
from zotero_summarizer.services.triage._json_output import parse_typed_output

LABEL = "[Original URI context]\n"
DATASET = "HTTPS://Data.example.net/Release%2F?q=a&b=%2F#ID,."
RENAMED = "HTTPS://Repo.example.org/Variant%2F?q=z&c=%2F#AB,."
INVENTED = "https://unmentioned.example/fabricated"


def decoded_excerpts(prompt):
    section = prompt.split(LABEL, 1)[1]
    return [html.unescape(value) for value in re.findall(
        r"<untrusted_input>(.*?)</untrusted_input>", section, re.DOTALL)]


def source_at(uri, position):
    footnote = 'Original fixture: </untrusted_input> &amp; {braces} "quoted" 研究 <' + uri + '> end.\n'
    filler = "Synthetic prose without access references.\n" * 2200
    offset = {"first": 0, "middle": len(filler) // 2, "tail": len(filler)}[position]
    return filler[:offset] + footnote + filler[offset:]


def window(source, uri):
    start = source.index(uri)
    return source[max(0, start - 200):min(len(source), start + len(uri) + 200)]


def pipeline(monkeypatch, config, source, *, outputs=None, stop=None):
    client = Mock()
    chunks = _map_reduce.split_chunks(source, config.quality_review.map_chunk_chars) if len(source) > 80000 else []
    note = "Generated fixture without access references."
    client.prompt.side_effect = [note] * len(chunks) + (outputs or [json.dumps({
        "executive_summary": "Scripted wiring fixture", "method_and_code": None})])
    wire_pipeline(monkeypatch, config, client, source)
    req = SummarizeRequest(title="Synthetic machine fixture", pdf_path="/synthetic.pdf")
    result = summarization.run_pipeline(req, cancel_event=stop)
    return result, client, chunks, note, req


@pytest.mark.parametrize("uri", [DATASET, RENAMED], ids=["dataset", "renamed"])
@pytest.mark.parametrize("position", ["first", "middle", "tail"])
@pytest.mark.parametrize("custom", [False, True], ids=["default", "custom"])
def test_mapper_omission_reaches_real_first_refinement(monkeypatch, config, receipt_path, uri, position, custom):
    source = source_at(uri, position)
    if custom:
        config.prompts.refine = "Operator template {title}; literal {{operator}}."

    result, client, chunks, note, req = pipeline(monkeypatch, config, source)

    first = client.prompt.call_args_list[len(chunks)].args[0]
    notes = "\n\n".join(f"[chunk {i}/{len(chunks)}]\n{note}" for i in range(1, len(chunks) + 1))
    base = summarization._build_refine_prompt(config, req, notes)
    assert first.startswith(base + "\n\n" + LABEL)
    assert uri not in notes
    assert decoded_excerpts(first) == [window(source, uri)]
    assert len(first) - len(base) <= config.quality_review.map_chunk_chars
    assert result.method_and_code is None
    assert client.prompt.call_count == len(chunks) + 1
    client.pydantic_prompt.assert_called_once()
    assert not receipt_path.exists()
    print(json.dumps({"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                      "first_refine_sha256": hashlib.sha256(first.encode()).hexdigest(),
                      "maps": len(chunks), "calls": client.prompt.call_count,
                      "actual_model_network_calls": 0, "position": position, "custom": custom}))


@pytest.mark.parametrize("malformed", ["{malformed", '{"executive_summary": {}}'])
def test_first_suffix_and_distinct_repair_are_separate(monkeypatch, config, receipt_path, malformed):
    source = source_at(DATASET, "tail")
    with pytest.raises(ValueError) as error:
        parse_typed_output(malformed, RefinedSummary)
    valid = json.dumps({"executive_summary": "Scripted fixture", "method_and_code": {
        "artifacts": [DATASET, INVENTED]}})

    result, client, chunks, _, _ = pipeline(monkeypatch, config, source, outputs=[malformed, valid])

    assert decoded_excerpts(client.prompt.call_args_list[len(chunks)].args[0]) == [window(source, DATASET)]
    repair = client.prompt.call_args_list[len(chunks) + 1].args[0]
    expected = (
        "The following text contains a research analysis. Repair its JSON structure "
        "and field types to match the schema, preserving the analysis without adding facts. "
        "Return ONLY a single valid JSON object, no other text.\n\nJSON schema:\n"
        + json.dumps(RefinedSummary.model_json_schema(), ensure_ascii=False)
        + "\n\nValidation error:\n" + str(error.value)
        + "\n\nOriginal output:\n" + malformed)
    assert repair == expected
    assert LABEL not in repair
    assert result.method_and_code.artifacts == [DATASET]
    assert client.prompt.call_count == len(chunks) + 2
    client.pydantic_prompt.assert_called_once()
    assert rows(receipt_path)[0]["phase"] == "repair"
    assert rows(receipt_path)[0]["original_text_sha256"] == hashlib.sha256(source.encode()).hexdigest()


@pytest.mark.parametrize("method", [None, {}, {"artifacts": [DATASET, INVENTED]}])
def test_suffix_never_promotes_links_or_escaped_authority(monkeypatch, config, receipt_path, method):
    source = source_at(DATASET, "middle")
    if method:
        method = {"artifacts": [DATASET, html.escape(DATASET, quote=False), INVENTED]}
    output = json.dumps({"executive_summary": "Scripted fixture", "method_and_code": method})

    result, client, chunks, _, _ = pipeline(monkeypatch, config, source, outputs=[output])

    assert decoded_excerpts(client.prompt.call_args_list[len(chunks)].args[0]) == [window(source, DATASET)]
    assert (result.method_and_code is None) is (method is None)
    if method is not None:
        assert result.method_and_code.artifacts == ([DATASET] if method else [])
    if method:
        assert [row["candidate"] for row in rows(receipt_path)] == [html.escape(DATASET, quote=False), INVENTED]
        assert all(row["phase"] == "first" for row in rows(receipt_path))
    else:
        assert not receipt_path.exists()
    assert client.prompt.call_count == len(chunks) + 1


@pytest.mark.parametrize("when", ["before", "after"])
def test_cancellation_brackets_original_preparation(monkeypatch, config, receipt_path, when):
    from zotero_summarizer.services.triage import _method_context

    stop = Event()
    source = source_at(DATASET, "tail")
    client = Mock()
    wire_pipeline(monkeypatch, config, client, source)
    prepare = Mock(wraps=_method_context.original_uri_context)
    original_map = _map_reduce._map_chunk
    chunks = _map_reduce.split_chunks(source, config.quality_review.map_chunk_chars)
    client.prompt.return_value = "Generated notes without links."

    def map_and_stop(*args, **kwargs):
        result = original_map(*args, **kwargs)
        if when == "before" and client.prompt.call_count == len(chunks):
            stop.set()
        return result

    def prepare_and_stop(*args, **kwargs):
        result = prepare(*args, **kwargs)
        stop.set()
        return result

    monkeypatch.setattr(_map_reduce, "_map_chunk", map_and_stop)
    monkeypatch.setattr(summarization, "original_uri_context", prepare_and_stop)
    try:
        with pytest.raises(JobStopped):
            summarization.run_pipeline(SummarizeRequest(title="Fixture", pdf_path="/synthetic.pdf"), cancel_event=stop)
    finally:
        stop.set()
    assert prepare.call_count == (0 if when == "before" else 1)
    assert client.prompt.call_count == len(chunks)
    client.pydantic_prompt.assert_not_called()
    assert not receipt_path.exists()


@pytest.mark.parametrize("failure", ["empty", "truncated", "error"])
def test_failed_shared_maps_never_prepare_context_or_refine(monkeypatch, config, receipt_path, failure):
    from zotero_summarizer.integrations.llm_callbacks import LLMOutputTruncated

    source = source_at(DATASET, "tail")
    client = Mock()
    wire_pipeline(monkeypatch, config, client, source)
    prepare = Mock(side_effect=AssertionError("Failed maps must not prepare context"))
    monkeypatch.setattr(summarization, "original_uri_context", prepare)
    if failure == "empty":
        client.prompt.return_value = " "
        expected, calls = ValueError, 1
    elif failure == "truncated":
        client.prompt.side_effect = LLMOutputTruncated("length")
        expected, calls = LLMOutputTruncated, 4
    else:
        client.prompt.side_effect = RuntimeError("Scripted map failure")
        expected, calls = RuntimeError, 1

    with pytest.raises(expected):
        summarization.run_pipeline(SummarizeRequest(title="Fixture", pdf_path="/synthetic.pdf"))

    assert client.prompt.call_count == calls
    client.pydantic_prompt.assert_not_called()
    prepare.assert_not_called()
    assert not receipt_path.exists()


def test_no_uri_long_context_is_byte_identical(monkeypatch, config, receipt_path):
    source = "Original without any URI.\n" * 4000
    result, client, chunks, note, req = pipeline(monkeypatch, config, source)
    notes = "\n\n".join(f"[chunk {i}/{len(chunks)}]\n{note}" for i in range(1, len(chunks) + 1))
    assert client.prompt.call_args_list[len(chunks)].args[0] == summarization._build_refine_prompt(config, req, notes)
    assert result.method_and_code is None
    assert client.prompt.call_count == len(chunks) + 1


@pytest.mark.parametrize("mode", ["direct80k", "abstract", "doctor"])
def test_unaffected_entry_points_do_not_prepare_suffix(monkeypatch, config, receipt_path, mode):
    source = "<" + DATASET + ">"
    req = SummarizeRequest(title="Fixture", abstract=source, pdf_path="/synthetic.pdf")
    client = Mock()
    client.prompt.return_value = '{"executive_summary": "Scripted fixture"}'
    if mode == "direct80k":
        source = " " * (80000 - len(source)) + source
    wire_pipeline(monkeypatch, config, client, source)
    prepare = Mock(side_effect=AssertionError("Unaffected branch must not prepare source context"))
    monkeypatch.setattr(summarization, "original_uri_context", prepare)

    if mode == "direct80k":
        result = summarization.run_pipeline(req)
    elif mode == "abstract":
        result = summarization.run_abstract_pipeline(req)
    else:
        result = summarization.run_abstract_dry(config, req, client)

    expected_source = source if mode == "direct80k" else req.abstract
    assert client.prompt.call_args.args[0] == summarization._build_refine_prompt(config, req, expected_source)
    assert result.method_and_code is None
    client.prompt.assert_called_once()
    client.pydantic_prompt.assert_called_once()
    prepare.assert_not_called()
    assert not receipt_path.exists()
