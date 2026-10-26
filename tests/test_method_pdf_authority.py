"""Owned PDF authority through controlled real pipeline stages; no model/HTTP."""
import builtins
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import fitz
import pytest

from zotero_summarizer.api.errors import APIError, ExtractionError
from zotero_summarizer.integrations import pdf
from zotero_summarizer.models import RefinedSummary, SummarizeRequest, TriageResult
from zotero_summarizer.services import corpus
from zotero_summarizer.services.library._map_reduce import split_chunks
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.triage import _method_admission, summarization

MEMBER = "https://example.org/reusable?x=%2F#Exact."
INVENTED = "https://example.org/invented"
METADATA = "https://example.org/metadata"


@pytest.fixture(scope="module", params=[False, True], ids=["direct", "map"])
def selected_pdf(request, tmp_path_factory):
    path = tmp_path_factory.mktemp("selected-onprem") / "owned.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Synthetic reusable method\nhttps:\n//example.org/reusable")
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 66, 230, 80), "uri": MEMBER})
        for _ in range(40 if request.param else 1):
            page = doc.new_page()
            page.insert_text((72, 72), "\n".join(["Synthetic original methods and results with conditional source evidence. " * 2] * 40), fontsize=7)
        doc.save(path)
    # The suite stubs OnPrem; this fresh interpreter uses the installed selected loader.
    result = subprocess.run([sys.executable, "-c",
        "import json,sys; from onprem.ingest.base import load_single_document; "
        "from zotero_summarizer.integrations.pdf import OnPremPdfExtractor; "
        "print(json.dumps(OnPremPdfExtractor(load_single_document).extract_text(sys.argv[1])))", str(path)],
        check=True, capture_output=True, text=True)
    text = json.loads(result.stdout.strip().splitlines()[-1])
    assert MEMBER not in text
    assert (len(text) > 80000) is request.param
    print(json.dumps({"selected_pdf": str(path), "selected_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                      "document_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "characters": len(text),
                      "selected_backend": "actual OnPrem plain", "model_http_calls": 0}))
    return path, text


def wire(monkeypatch, tmp_path, text):
    config = _default_goals_config()
    client = Mock()
    extractor = pdf.OnPremPdfExtractor(lambda path, **kwargs: [SimpleNamespace(page_content=text)])
    app = SimpleNamespace(app_state=SimpleNamespace(config=config), pdf_extractor=extractor)
    monkeypatch.setattr(summarization.factory, "resolve_stage_client", lambda app, stage: client)
    monkeypatch.setattr(summarization, "state", lambda: app)
    monkeypatch.setattr(summarization, "settings", lambda: SimpleNamespace(pdf_root=tmp_path))
    monkeypatch.setattr(_method_admission, "settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(corpus, "run_corpus_match", lambda *args: corpus.empty_corpus_match_result())
    client.pydantic_prompt.return_value = TriageResult(score=3, rationale="Controlled fixture")
    return config, client


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_selected_onprem_pdf_authority_survives_direct_map_first_repair(monkeypatch, tmp_path, selected_pdf, repair):
    path, text = selected_pdf
    # Confine the selected original without altering its bytes.
    config, client = wire(monkeypatch, path.parent, text)
    monkeypatch.setattr(_method_admission, "settings", lambda: SimpleNamespace(data_dir=tmp_path))
    chunks = split_chunks(text, config.quality_review.map_chunk_chars) if len(text) > 80000 else []
    client.prompt.side_effect = ["Generated notes " + INVENTED] * len(chunks) + (["{malformed"] if repair else []) + [
        json.dumps({"executive_summary": "Controlled fixture", "method_and_code": {
            "what_it_does": "Unchanged operation", "artifacts": [MEMBER, METADATA, INVENTED, MEMBER]}})]
    req = SummarizeRequest(title="Synthetic " + METADATA, pdf_path=str(path))
    result = summarization.run_pipeline(req)
    print(json.dumps({"phase": "repair" if repair else "first", "maps": len(chunks),
                      "calls": client.prompt.call_count, "artifacts": result.method_and_code.artifacts}))
    assert result.method_and_code.artifacts == [MEMBER, METADATA, MEMBER]
    assert result.method_and_code.what_it_does == "Unchanged operation"
    assert client.prompt.call_count == len(chunks) + (2 if repair else 1)
    client.pydantic_prompt.assert_called_once()
    assert all(MEMBER not in call.args[0] for call in client.prompt.call_args_list)
    records = [json.loads(line) for line in (tmp_path / "method-artifact-rejections.jsonl").read_text().splitlines()]
    assert [row["candidate"] for row in records] == [INVENTED]
    assert records[0]["phase"] == ("repair" if repair else "first")
    assert records[0]["original_text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert records[0]["original_document_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert records[0]["annotation_authority_sha256"] == hashlib.sha256(json.dumps(
        [MEMBER], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert "annotation_uris" not in records[0]


def _payload(artifacts):
    return RefinedSummary(executive_summary="Controlled fixture", method_and_code={"artifacts": artifacts}).model_dump_json()


def invoke(config, client, source, *, text=None):
    return summarization._refine_with_retry(client, config, SummarizeRequest(title="Synthetic fixture"),
        source.text, "fixture", original_text=source.text if text is None else text, original_pdf=source)


@pytest.fixture
def private_source(tmp_path):
    path = tmp_path / "owned.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Synthetic source without visible URLs")
        doc.save(path)
    return pdf.capture_pdf_source(path, SimpleNamespace(extract_text=lambda p: "Original without links"))


@pytest.mark.parametrize("uri", [
    "HTTPS://EXAMPLE.org/Project", "https://example.org/a?x=%2F&y=1#Part.",
    "https://example.org/a%2f", "https://example.org/trailing.", "https://example.org/trailing,",
    "https://example.org/repo's-code", "https://example.org/trailing'", "https://example.org/unpaired)",
    "https://example.org/unpaired(", "https://example.org/(balanced)",
])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_whole_opaque_annotations_retain_literal_identity(monkeypatch, tmp_path, private_source, uri, repair):
    config, client = wire(monkeypatch, tmp_path, private_source.text)
    source = pdf.PdfSource(private_source.text, (uri, uri), private_source.document_sha256)
    client.prompt.side_effect = (["{malformed"] if repair else []) + [_payload([uri, uri])]
    result = invoke(config, client, source)
    assert result.method_and_code.artifacts == [uri, uri]
    assert client.prompt.call_count == (2 if repair else 1)
    assert not (tmp_path / "method-artifact-rejections.jsonl").exists()


@pytest.mark.parametrize("annotation,candidate", [
    (" " + MEMBER + " ", MEMBER), ("See " + MEMBER, MEMBER), ("<" + MEMBER + ">", MEMBER),
    ('"' + MEMBER + '"', MEMBER), ("'" + MEMBER + "'", MEMBER), ("`" + MEMBER + "`", MEMBER),
    ("ftp://example.org/?redirect=" + MEMBER, MEMBER), ("https:/example.org/?redirect=" + MEMBER, MEMBER),
    ("https://outer.example/?redirect=" + MEMBER, MEMBER), ("", MEMBER), ("https://", MEMBER),
    (MEMBER + "\nmore", MEMBER), (MEMBER + "\x00", MEMBER), (MEMBER + "\x7f", MEMBER),
    (MEMBER + "\u200b", MEMBER), (MEMBER + '"', MEMBER), (MEMBER + "`", MEMBER),
    (MEMBER + "<", MEMBER), (MEMBER + ">", MEMBER), (MEMBER, MEMBER.lower()),
    ("https://example.org/a%2F", "https://example.org/a/"),
    ("https://example.org/a%2F", "https://example.org/a%2f"),
    ("https://example.org/trailing.", "https://example.org/trailing"),
    ("https://example.org/trailing,", "https://example.org/trailing"),
    ("https://[malformed", MEMBER),
])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_annotations_never_unwrap_trim_normalize_or_authorize_nested_suffix(
    monkeypatch, tmp_path, private_source, annotation, candidate, repair,
):
    config, client = wire(monkeypatch, tmp_path, private_source.text)
    source = pdf.PdfSource(private_source.text, (annotation,), private_source.document_sha256)
    client.prompt.side_effect = (["{malformed"] if repair else []) + [_payload([candidate])]
    assert invoke(config, client, source).method_and_code.artifacts == []
    assert client.prompt.call_count == (2 if repair else 1)
    record = json.loads((tmp_path / "method-artifact-rejections.jsonl").read_text())
    assert record["candidate"] == candidate
    assert record["original_document_sha256"] == source.document_sha256
    assert record["annotation_authority_sha256"] == hashlib.sha256(json.dumps(
        source.annotation_uris, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@pytest.mark.parametrize("uris,expected", [
    ((), "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
    (("https://example.org/a",), "d1e6244a2c7d9fc940d98849ff4a0e5e14418243ed9a791d34e582067a450cd9"),
    (("https://example.org/a", "https://example.org/a"), "32636514fce7158128bbe76e2c39158914b3ef8c236c00ce27be4888002ffe6e"),
    (("https://example.org/a", "https://example.org/b"), "55fd82f401c180c2746513cd9b3d473412746295287d99684881667a8ba12a7b"),
    (("https://example.org/b", "https://example.org/a"), "ce13b236fba64779135b02092e37949f91207ec2b6bbab462b2611def3148088"),
    (("https://example.org/a?x=%2F&y=1#Fragment.", " https://example.org/not-authority ", "https://例え.テスト/a"),
     "7d43eaf92589152c442560d2e9895b983741205c9973b42c723e7dd8eaaae737"),
])
def test_pinned_canonical_tuple_hash_vectors_on_actual_receipts(monkeypatch, tmp_path, private_source, uris, expected):
    config, client = wire(monkeypatch, tmp_path, private_source.text)
    source = pdf.PdfSource(private_source.text, uris, private_source.document_sha256)
    client.prompt.return_value = _payload([INVENTED])
    assert invoke(config, client, source).method_and_code.artifacts == []
    record = json.loads((tmp_path / "method-artifact-rejections.jsonl").read_text())
    assert record["annotation_authority_sha256"] == expected
    encoded = json.dumps(uris, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == expected
    print(json.dumps({"tuple": uris, "canonical_utf8_hex": encoded.hex(), "receipt_sha256": record["annotation_authority_sha256"]}))


@pytest.mark.parametrize("mismatch", ["different-text", "wrong-type"])
def test_pdf_authority_conflict_fails_before_first_model_call(monkeypatch, tmp_path, private_source, mismatch):
    config, client = wire(monkeypatch, tmp_path, private_source.text)
    source = private_source if mismatch == "different-text" else SimpleNamespace(text=private_source.text)
    with pytest.raises((ValueError, TypeError)):
        invoke(config, client, source, text="Conflicting original" if mismatch == "different-text" else private_source.text)
    client.prompt.assert_not_called()
    assert not (tmp_path / "method-artifact-rejections.jsonl").exists()


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_pdf_receipt_failure_propagates_without_extra_repair(monkeypatch, tmp_path, private_source, repair):
    config, client = wire(monkeypatch, tmp_path, private_source.text)
    client.prompt.side_effect = (["{malformed"] if repair else []) + [_payload([INVENTED])]
    (tmp_path / "method-artifact-rejections.jsonl").mkdir()
    with pytest.raises(OSError):
        invoke(config, client, private_source)
    assert client.prompt.call_count == (2 if repair else 1)


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_captured_source_is_pinned_when_file_changes_in_model_stage(monkeypatch, tmp_path, selected_pdf, repair):
    original, text = selected_pdf
    path = tmp_path / "owned.pdf"
    shutil.copyfile(original, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    config, client = wire(monkeypatch, tmp_path, text)
    chunks = split_chunks(text, config.quality_review.map_chunk_chars) if len(text) > 80000 else []
    calls, captured, supplied = [], [], []
    capture, refine = summarization.capture_pdf_source, summarization._refine_with_retry

    def observe_capture(*args):
        captured.append(capture(*args))
        return captured[-1]

    def observe_refine(*args, **kwargs):
        supplied.append(kwargs["original_pdf"])
        return refine(*args, **kwargs)

    def output(prompt):
        calls.append(prompt)
        path.write_bytes(b"Model-stage changed source")
        if len(calls) <= len(chunks):
            return "Generated note " + INVENTED
        if repair and len(calls) == len(chunks) + 1:
            return "{malformed"
        return _payload([MEMBER, INVENTED])

    monkeypatch.setattr(summarization, "capture_pdf_source", observe_capture)
    monkeypatch.setattr(summarization, "_refine_with_retry", observe_refine)
    client.prompt.side_effect = output
    result = summarization.run_pipeline(SummarizeRequest(title="Synthetic fixture", pdf_path=str(path)))
    assert result.method_and_code.artifacts == [MEMBER]
    assert len(captured) == 1 and supplied[0] is captured[0]
    assert captured[0].document_sha256 == digest
    assert client.prompt.call_count == len(chunks) + (2 if repair else 1)
    record = json.loads((tmp_path / "method-artifact-rejections.jsonl").read_text())
    assert record["original_document_sha256"] == digest
    assert hashlib.sha256(path.read_bytes()).hexdigest() != digest


@pytest.mark.parametrize("failure", ["reader", "extractor", "hash", "replacement", "empty", "wrong-text-type",
                                     "outside-root", "missing", "uninitialized", "reader-schema"])
def test_pipeline_capture_errors_precede_corpus_and_model(monkeypatch, tmp_path, private_source, failure):
    path = tmp_path / "owned.pdf"
    config, client = wire(monkeypatch, tmp_path, private_source.text)
    app = summarization.state()
    corpus_call = Mock(side_effect=AssertionError("Corpus must not run"))
    monkeypatch.setattr(corpus, "run_corpus_match", corpus_call)
    if failure == "reader":
        monkeypatch.setattr(fitz, "open", Mock(side_effect=RuntimeError("reader failed")))
    elif failure == "hash":
        monkeypatch.setattr(pdf, "_file_fingerprint", Mock(side_effect=OSError("hash failed")))
    elif failure == "outside-root":
        monkeypatch.setattr(summarization, "settings", lambda: SimpleNamespace(pdf_root=tmp_path / "other"))
    elif failure == "missing":
        path.unlink()
    elif failure == "uninitialized":
        app.pdf_extractor = None
    elif failure == "reader-schema":
        monkeypatch.setattr(pdf, "raw_link_uris", Mock(side_effect=TypeError("Malformed reader data")))
    elif failure == "extractor":
        app.pdf_extractor = SimpleNamespace(extract_text=Mock(side_effect=RuntimeError("extractor failed")))
    elif failure == "replacement":
        def replace(selected):
            other = tmp_path / "other.pdf"
            shutil.copyfile(path, other)
            other.replace(path)
            return private_source.text
        app.pdf_extractor = SimpleNamespace(extract_text=replace)
    else:
        app.pdf_extractor = SimpleNamespace(extract_text=lambda p: " " if failure == "empty" else None)
    with pytest.raises((RuntimeError, ValueError, TypeError, OSError, APIError, ExtractionError)):
        summarization.run_pipeline(SummarizeRequest(title="Synthetic fixture", pdf_path=str(path)))
    client.prompt.assert_not_called()
    corpus_call.assert_not_called()


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
@pytest.mark.parametrize("dry", [False, True], ids=["live-abstract", "doctor"])
def test_abstract_modes_never_probe_pdf_and_keep_distinct_receipt_contracts(monkeypatch, tmp_path, repair, dry):
    config, client = wire(monkeypatch, tmp_path, "Abstract without URLs")
    real_import = builtins.__import__

    def forbidden(*args, **kwargs):
        raise AssertionError("Abstract must not access PDF reader or dry state/writer")

    def no_reader_import(name, *args, **kwargs):
        if name == "fitz":
            forbidden()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_reader_import)
    monkeypatch.setattr(summarization, "capture_pdf_source", forbidden)
    monkeypatch.setattr(pdf, "raw_link_uris", forbidden)
    client.prompt.side_effect = (["{malformed"] if repair else []) + [_payload([INVENTED])]
    req = SummarizeRequest(title="Synthetic fixture", abstract="Abstract without URLs")
    if dry:
        monkeypatch.setattr(summarization, "state", forbidden)
        monkeypatch.setattr(summarization, "settings", forbidden)
        monkeypatch.setattr(_method_admission, "settings", forbidden)
        monkeypatch.setattr(_method_admission.run_log, "append_run", forbidden)
        with pytest.raises(ValueError, match="Write-free refinement rejected"):
            summarization.run_abstract_dry(config, req, client)
        client.pydantic_prompt.assert_not_called()
        assert not (tmp_path / "method-artifact-rejections.jsonl").exists()
    else:
        assert summarization.run_abstract_pipeline(req).method_and_code.artifacts == []
        record = json.loads((tmp_path / "method-artifact-rejections.jsonl").read_text())
        assert set(record) == {"schema_version", "original_text_sha256", "metadata_sha256", "phase", "field", "candidate", "reason", "lexical_grammar"}
    assert client.prompt.call_count == (2 if repair else 1)


def test_private_extraction_has_no_text_only_alias():
    assert not hasattr(summarization, "_extract_pdf_text")
    root = Path(__file__).resolve().parents[1]
    assert Path(pdf.__file__).resolve() == root / "zotero_summarizer/integrations/pdf.py"
