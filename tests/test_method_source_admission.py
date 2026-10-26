"""Synthetic machine URI identities through the real refinement boundary; no inference."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_summarizer.integrations.pdf import PdfSource
from zotero_summarizer.models import GoalsConfig, RefinedSummary, SummarizeRequest, SummarizeResponse, TriageResult
from zotero_summarizer.services import corpus
from zotero_summarizer.services.triage import _method_admission, summarization
from zotero_summarizer.services.triage._execution import JobStopped
from zotero_summarizer.services.zotero.pending import build_triage_note_html


SOURCE = "Public synthetic source: contribution code https://example.org/project."
MEMBER = "https://example.org/project"
INVENTED = "https://invented.example/unmentioned"


@pytest.fixture
def config():
    return GoalsConfig(relevance_scale={i: str(i) for i in range(1, 6)}, llm={
        "draft_model": "stub", "refine_model": "stub", "api_base": "http://stub",
        "api_key_env": "STUB_KEY",
    })


@pytest.fixture
def receipt_path(monkeypatch, tmp_path):
    monkeypatch.setattr(_method_admission, "settings", lambda: SimpleNamespace(data_dir=tmp_path))
    return tmp_path / "method-artifact-rejections.jsonl"


def payload(artifacts):
    return json.dumps({"executive_summary": "Synthetic fixture", "method_and_code": {
        "artifacts": artifacts,
    }})


def refine(client, config, source=SOURCE, req=None):
    return summarization._refine_with_retry(
        client, config, req or SummarizeRequest(title="Synthetic source-membership fixture"),
        source, "method-membership-probe", original_text=source,
    )


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("case,candidate,repair,expected", [
    ("invented_absolute", INVENTED, False, []),
    ("prefix_nonmember", MEMBER + "-extra", False, []),
    ("repair_invented_absolute", INVENTED, True, []),
    ("exact_member_control", MEMBER, False, [MEMBER]),
])
def test_parent_machine_reproduction(config, receipt_path, case, candidate, repair, expected):
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([candidate])]
    result = refine(client, config)
    digest = hashlib.sha256(SOURCE.encode()).hexdigest()
    print(json.dumps({
        "case": case, "logical_calls": client.prompt.call_count,
        "returned_artifacts": result.method_and_code.artifacts,
        "original_text_sha256": digest, "source": SOURCE, "actual_model_or_http_calls": 0,
        "scope": "Machine URI identity only; no free-text relevance verdict.",
    }))
    assert client.prompt.call_count == (2 if repair else 1)
    assert result.method_and_code.artifacts == expected
    if expected:
        assert not receipt_path.exists()
    else:
        recorded = rows(receipt_path)
        assert len(recorded) == 1
        assert recorded[0]["candidate"] == candidate
        assert recorded[0]["phase"] == ("repair" if repair else "first")
        assert recorded[0]["field"] == "method_and_code.artifacts[0]"
        assert recorded[0]["original_text_sha256"] == digest
        assert recorded[0]["reason"] == "not_original_uri"
        assert recorded[0]["schema_version"] == 1
        print(json.dumps({"receipt_read_back_from": str(receipt_path), "rows": recorded}))


@pytest.mark.parametrize("source,candidate,admitted", [
    (MEMBER + "-extra", MEMBER, False),
    ("x" + MEMBER, MEMBER, False),
    (MEMBER + ".", MEMBER, True),
    (MEMBER + ", next", MEMBER, True),
    (MEMBER + "..", MEMBER, False),
    (MEMBER + ".,", MEMBER + ".", False),
    ("<" + MEMBER + ".>", MEMBER, False),
    ("<" + MEMBER + ".>", MEMBER + ".", True),
    ('"' + MEMBER + ',"', MEMBER + ",", True),
    ("'" + MEMBER + ".'", MEMBER + ".", True),
    ("[code](" + MEMBER + ".)", MEMBER + ".", True),
    ("[code](" + MEMBER + ".)", MEMBER, False),
    ("(" + MEMBER + ").", MEMBER, True),
    (MEMBER + "_(v1)", MEMBER + "_(v1)", True),
    ("[code](" + MEMBER + "_(v1))", MEMBER + "_(v1)", True),
    (MEMBER + "?x=a.", MEMBER + "?x=a.", True),
    (MEMBER + "?x=a.", MEMBER + "?x=a", False),
    (MEMBER + "?x=a,b#id,", MEMBER + "?x=a,b#id,", True),
    (MEMBER + ".md", MEMBER + ".md", True),
    (MEMBER + "%2Fdata", MEMBER + "%2fdata", False),
    (MEMBER + "%2Fdata", MEMBER + "%2Fdata", True),
    ("HTTPS://EXAMPLE.org/Project", "HTTPS://EXAMPLE.org/Project", True),
    ("HTTPS://EXAMPLE.org/Project", "https://example.org/Project", False),
    ("[code](" + MEMBER + "?next=" + INVENTED + ")", INVENTED, False),
    ("<" + MEMBER, MEMBER, False),
])
def test_complete_verbatim_lexical_grammar(config, receipt_path, source, candidate, admitted):
    client = Mock()
    client.prompt.return_value = payload([candidate])
    result = refine(client, config, source)
    assert result.method_and_code.artifacts == ([candidate] if admitted else [])
    client.prompt.assert_called_once()
    assert receipt_path.exists() is not admitted


@pytest.mark.parametrize("field", ["title", "doi", "abstract"])
def test_only_actually_bound_metadata_can_authorize(config, receipt_path, field):
    req = SummarizeRequest(**{"title": "Fixture", field: MEMBER})
    client = Mock()
    client.prompt.return_value = payload([MEMBER, INVENTED])
    result = refine(client, config, "Original source without links.", req)
    assert result.method_and_code.artifacts == [MEMBER]
    recorded = rows(receipt_path)
    assert recorded[0]["field"] == "method_and_code.artifacts[1]"
    metadata = {"title": req.title, "doi": req.doi, "abstract": req.abstract}
    digest = hashlib.sha256(json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    assert recorded[0]["metadata_sha256"] == digest


def test_bare_doi_does_not_authorize_manufactured_uri(config, receipt_path):
    req = SummarizeRequest(title="Fixture", doi="10.0000/synthetic")
    client = Mock()
    client.prompt.return_value = payload(["https://doi.org/10.0000/synthetic"])
    assert refine(client, config, "Original source", req).method_and_code.artifacts == []


def wire_pipeline(monkeypatch, config, client, source):
    app = SimpleNamespace(app_state=SimpleNamespace(config=config))
    monkeypatch.setattr(summarization.factory, "resolve_stage_client", lambda app, stage: client)
    monkeypatch.setattr(summarization, "state", lambda: app)
    monkeypatch.setattr(summarization, "_extract_pdf_source", lambda path: PdfSource(
        source, (), hashlib.sha256(b"synthetic PDF seam fixture").hexdigest()))
    monkeypatch.setattr(corpus, "run_corpus_match", lambda *args: corpus.empty_corpus_match_result())
    client.pydantic_prompt.return_value = TriageResult(score=3, rationale="Fixture assessment")


@pytest.mark.parametrize("repair", [False, True])
def test_long_map_notes_cannot_authorize_and_original_tail_survives(
    monkeypatch, config, receipt_path, repair,
):
    from zotero_summarizer.services.library._map_reduce import split_chunks

    source = "Synthetic original paragraph.\n" * 3000 + "\n<" + MEMBER + ">"
    assert len(source) > 80000
    chunks = split_chunks(source, config.quality_review.map_chunk_chars)
    note = "Generated note containing " + INVENTED
    client = Mock()
    client.prompt.side_effect = [note] * len(chunks) + (["{malformed"] if repair else []) + [
        payload([MEMBER, INVENTED]),
    ]
    wire_pipeline(monkeypatch, config, client, source)
    result = summarization.run_pipeline(SummarizeRequest(title="Map fixture", pdf_path="/synthetic.pdf"))
    assert result.method_and_code.artifacts == [MEMBER]
    assert client.prompt.call_count == len(chunks) + (2 if repair else 1)
    assert client.pydantic_prompt.call_count == 1
    refine_prompt = client.prompt.call_args_list[len(chunks)].args[0]
    assert __import__("html").unescape(refine_prompt.split("[Original URI context]\n", 1)[1].split(
        "<untrusted_input>", 1)[1].split("</untrusted_input>", 1)[0]) == source[source.index(MEMBER) - 200:] and INVENTED in refine_prompt
    record = rows(receipt_path)[0]
    assert record["original_text_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert record["phase"] == ("repair" if repair else "first")
    assert record["candidate"] == INVENTED


@pytest.mark.parametrize("mode", ["direct80k", "abstract"])
def test_live_entry_points_keep_original_authority(monkeypatch, config, receipt_path, mode):
    source = SOURCE if mode != "direct80k" else " " * (80000 - len(SOURCE)) + SOURCE
    client = Mock()
    client.prompt.return_value = payload([MEMBER, INVENTED])
    wire_pipeline(monkeypatch, config, client, source)
    req = SummarizeRequest(title="Fixture", abstract=source, pdf_path="/synthetic.pdf")
    if mode == "direct80k":
        result = summarization.run_pipeline(req)
    else:
        result = summarization.run_abstract_pipeline(req)
    assert result.method_and_code.artifacts == [MEMBER]
    client.prompt.assert_called_once()
    client.pydantic_prompt.assert_called_once()
    assert rows(receipt_path)[0]["original_text_sha256"] == hashlib.sha256(source.encode()).hexdigest()


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("method", [None, {}, {"artifacts": [MEMBER]}, {"artifacts": [INVENTED]}])
def test_dry_membership_first_and_repair_never_access_app_state_or_write(
    monkeypatch, config, repair, method,
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Write-free Doctor must not access app state or receipt I/O")

    monkeypatch.setattr(_method_admission, "settings", forbidden)
    monkeypatch.setattr(_method_admission.run_log, "append_run", forbidden)
    monkeypatch.setattr(summarization, "state", forbidden)
    client = Mock()
    output = json.dumps({"executive_summary": "Fixture", "method_and_code": method})
    client.prompt.side_effect = (["{malformed"] if repair else []) + [output]
    client.pydantic_prompt.return_value = TriageResult(score=3, rationale="Fixture")
    req = SummarizeRequest(title="Dry fixture", abstract=SOURCE)
    if method and method["artifacts"] == [INVENTED]:
        with pytest.raises(ValueError, match="Write-free refinement rejected method artifact URI"):
            summarization.run_abstract_dry(config, req, client)
        client.pydantic_prompt.assert_not_called()
    else:
        result = summarization.run_abstract_dry(config, req, client)
        assert (result.method_and_code is None) is (method is None)
        if method is not None:
            assert result.method_and_code.artifacts == method.get("artifacts", [])
        client.pydantic_prompt.assert_called_once()
    assert client.prompt.call_count == (2 if repair else 1)


@pytest.mark.parametrize("method", [None, {}, {"artifacts": [INVENTED]}])
def test_empty_null_and_artifact_only_never_become_error_default_null(config, receipt_path, method):
    client = Mock()
    client.prompt.return_value = json.dumps({"executive_summary": "Fixture", "method_and_code": method})
    result = refine(client, config)
    assert (result.method_and_code is None) is (method is None)
    if method is not None:
        assert result.method_and_code.artifacts == []
    client.prompt.assert_called_once()


@pytest.mark.parametrize("repair", [False, True])
def test_rejection_receipt_failure_propagates_without_repair_or_mutation(
    monkeypatch, config, receipt_path, repair,
):
    parsed = RefinedSummary.model_validate_json(payload([INVENTED]))
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([INVENTED])]
    original_parser = summarization.parse_typed_output

    def parse(text, model, **kwargs):
        original_parser(text, model, **kwargs)
        return parsed

    monkeypatch.setattr(summarization, "parse_typed_output", parse)
    receipt_path.mkdir()
    with pytest.raises(OSError):
        refine(client, config)
    assert parsed.method_and_code.artifacts == [INVENTED]
    assert client.prompt.call_count == (2 if repair else 1)


@pytest.mark.parametrize("outputs,calls,error", [
    ([RuntimeError("model failed")], 1, RuntimeError),
    (["{malformed", RuntimeError("repair model failed")], 2, RuntimeError),
    (["{malformed", "{malformed"], 2, ValueError),
    ([payload(["not an absolute URI"]), payload(["not an absolute URI"])], 2, ValueError),
])
def test_model_and_schema_failures_are_not_successful_null(config, receipt_path, outputs, calls, error):
    client = Mock()
    client.prompt.side_effect = outputs
    with pytest.raises(error):
        refine(client, config)
    assert client.prompt.call_count == calls
    assert not receipt_path.exists()


@pytest.mark.parametrize("repair", [False, True])
def test_cancellation_precedes_admission_and_new_effects(config, receipt_path, repair):
    stop = Event()
    client = Mock()

    def output(_):
        if repair and client.prompt.call_count == 1:
            return "{malformed"
        stop.set()
        return payload([INVENTED])

    client.prompt.side_effect = output
    with pytest.raises(JobStopped):
        summarization._refine_with_retry(client, config, SummarizeRequest(title="Fixture"), SOURCE,
                                          "fixture", stop, original_text=SOURCE)
    assert client.prompt.call_count == (2 if repair else 1)
    assert not receipt_path.exists()


def test_source_failure_stays_error_before_any_model_call(monkeypatch, config, receipt_path):
    client = Mock()
    wire_pipeline(monkeypatch, config, client, SOURCE)
    monkeypatch.setattr(summarization, "_extract_pdf_source", Mock(side_effect=ValueError("source failed")))
    with pytest.raises(ValueError, match="source failed"):
        summarization.run_pipeline(SummarizeRequest(title="Fixture", pdf_path="/synthetic.pdf"))
    client.prompt.assert_not_called()
    assert not receipt_path.exists()


def test_existing_json_reload_render_and_nonartifact_fields_are_preserved(config, receipt_path, tmp_path):
    fields = {"what_it_does": "Fixture operation", "what_is_new": "Fixture attribution",
              "how_it_works": ["Fixture step"], "evaluation": "Fixture uncertainty",
              "how_i_could_use_it": "Fixture proposed adaptation", "artifacts": [MEMBER, INVENTED, MEMBER]}
    client = Mock()
    client.prompt.return_value = json.dumps({"executive_summary": "Fixture", "method_and_code": fields})
    result = refine(client, config)
    assert result.method_and_code.model_dump() == {**fields, "artifacts": [MEMBER, MEMBER]}
    response = SummarizeResponse(**result.model_dump(), relevance_score=3, triage_rationale="Fixture")
    path = tmp_path / "summary.json"
    path.write_text(response.model_dump_json())
    restored = SummarizeResponse.model_validate_json(path.read_text())
    assert restored == response
    html = build_triage_note_html("Fixture", restored)
    assert MEMBER in html and INVENTED not in html
    for method in [None, {}, {"artifacts": [INVENTED]}]:
        legacy = SummarizeResponse(executive_summary="Legacy", relevance_score=2,
                                   triage_rationale="Legacy", method_and_code=method)
        assert SummarizeResponse.model_validate_json(legacy.model_dump_json()) == legacy


def test_concurrent_receipts_are_complete_and_append_only(config, receipt_path):
    def generate(index):
        client = Mock()
        client.prompt.return_value = payload([INVENTED + str(index)])
        return refine(client, config)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(generate, range(12)))
    assert all(result.method_and_code.artifacts == [] for result in results)
    recorded = rows(receipt_path)
    assert len(recorded) == 12
    assert {row["candidate"] for row in recorded} == {INVENTED + str(index) for index in range(12)}


def test_import_provenance():
    root = Path(__file__).resolve().parents[1]
    assert Path(summarization.__file__).resolve() == root / "zotero_summarizer/services/triage/summarization.py"
    assert Path(_method_admission.__file__).resolve() == root / "zotero_summarizer/services/triage/_method_admission.py"
    print(json.dumps({"summarization_import": summarization.__file__,
                      "admission_import": _method_admission.__file__}))


CONTAINING_URIS = [
    pytest.param("ftp://mirror.example/releases?redirect=" + MEMBER, id="ftp-query"),
    pytest.param("mailto:fixture@example.org?body=" + MEMBER, id="mailto-opaque"),
    pytest.param("mailto:" + MEMBER, id="opaque-colon"),
    pytest.param("data:text/plain," + MEMBER, id="data-opaque-control"),
    pytest.param("demo+v1.2-resource:payload=" + MEMBER, id="custom-rfc-scheme"),
    pytest.param("9ftp://mirror.example/?redirect=" + MEMBER, id="rejected-prefix"),
    pytest.param("1://mirror.example/?redirect=" + MEMBER, id="malformed-scheme"),
    pytest.param("1://mirror.example/?redirect=(" + MEMBER + ")", id="malformed-wrapped-suffix"),
    pytest.param("https:/mirror.example/?redirect=" + MEMBER, id="malformed-http"),
]


@pytest.mark.parametrize("source", CONTAINING_URIS)
@pytest.mark.parametrize("field", ["original", "title", "doi", "abstract"])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_containing_uri_never_authorizes_nested_http(config, receipt_path, source, field, repair):
    original = source if field == "original" else "Public synthetic source without links."
    req = SummarizeRequest(**{"title": "Containing URI fixture", **({field: source} if field != "original" else {})})
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER])]
    result = refine(client, config, original, req)
    digest = hashlib.sha256(original.encode()).hexdigest()
    print(json.dumps({"source": source, "field": field, "phase": "repair" if repair else "first",
                      "original_text_sha256": digest, "fixture_sha256": hashlib.sha256(source.encode()).hexdigest(),
                      "logical_calls": client.prompt.call_count, "returned_artifacts": result.method_and_code.artifacts,
                      "actual_model_http_calls": 0}))
    assert client.prompt.call_count == (2 if repair else 1)
    assert result.method_and_code.artifacts == []
    record = rows(receipt_path)[0]
    assert record["candidate"] == MEMBER and record["field"] == "method_and_code.artifacts[0]"
    assert record["phase"] == ("repair" if repair else "first")
    assert record["original_text_sha256"] == digest
    metadata = {"title": req.title, "doi": req.doi, "abstract": req.abstract}
    assert record["metadata_sha256"] == hashlib.sha256(json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


@pytest.mark.parametrize("source", CONTAINING_URIS)
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_dry_containing_uri_rejection_precedes_settings_state_writer(monkeypatch, config, source, repair):
    def forbidden(*args, **kwargs):
        raise AssertionError("Containing URI rejection must precede dry state or receipt I/O")

    monkeypatch.setattr(_method_admission, "settings", forbidden)
    monkeypatch.setattr(_method_admission.run_log, "append_run", forbidden)
    monkeypatch.setattr(summarization, "state", forbidden)
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER])]
    client.pydantic_prompt.return_value = TriageResult(score=3, rationale="Fixture")
    with pytest.raises(ValueError, match="Write-free refinement rejected method artifact URI"):
        summarization.run_abstract_dry(config, SummarizeRequest(title="Dry fixture", abstract=source), client)
    assert client.prompt.call_count == (2 if repair else 1)
    client.pydantic_prompt.assert_not_called()


@pytest.mark.parametrize("source", [
    pytest.param(MEMBER, id="standalone"),
    pytest.param("URL=" + MEMBER, id="plain-assignment"),
    pytest.param("ftp://mirror.example/?redirect=" + MEMBER + " <" + MEMBER + ">", id="independent-after-containing"),
    pytest.param("source_uri=" + MEMBER, id="other-assignment-name"),
    pytest.param("<" + MEMBER + ">", id="explicit-angle"),
    pytest.param("[code](" + MEMBER + ")", id="markdown-destination"),
])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_containing_scan_retains_complete_standalone_http(config, receipt_path, source, repair):
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER])]
    result = refine(client, config, source)
    assert result.method_and_code.artifacts == [MEMBER]
    assert client.prompt.call_count == (2 if repair else 1)
    assert not receipt_path.exists()


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_raw_map_containing_uri_is_not_original_http_authority(monkeypatch, config, receipt_path, repair):
    from zotero_summarizer.services.library._map_reduce import split_chunks

    source = "Synthetic original paragraph.\n" * 3000 + "\nftp://mirror.example/releases?redirect=" + MEMBER
    chunks = split_chunks(source, config.quality_review.map_chunk_chars)
    client = Mock()
    client.prompt.side_effect = ["Generated note: " + MEMBER] * len(chunks) + (
        ["{malformed"] if repair else []) + [payload([MEMBER])]
    wire_pipeline(monkeypatch, config, client, source)
    result = summarization.run_pipeline(SummarizeRequest(title="Map fixture", pdf_path="/synthetic.pdf"))
    assert result.method_and_code.artifacts == []
    assert client.prompt.call_count == len(chunks) + (2 if repair else 1)
    client.pydantic_prompt.assert_called_once()
    record = rows(receipt_path)[0]
    assert record["candidate"] == MEMBER
    assert record["original_text_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert record["phase"] == ("repair" if repair else "first")
