"""Literal destination scope and bounded traversal; synthetic inputs, no inference."""
import hashlib
import json
from unittest.mock import Mock

import pytest

from test_method_source_admission import MEMBER, config, payload, receipt_path, refine, rows, wire_pipeline
from zotero_summarizer.models import SummarizeRequest, TriageResult
from zotero_summarizer.services.triage import _method_admission, summarization


@pytest.mark.parametrize("label", ["code/repo", "Code:", "don't:", "owner's:'", '"opaque:', "<opaque:", "`opaque:", f"outer [don't:]({MEMBER})", f"don't:]({MEMBER}) [can't:", "\\[literal\\] don't:"])
@pytest.mark.parametrize("field", ["original", "title", "doi", "abstract"])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_balanced_destination_first_repair_metadata(config, receipt_path, label, field, repair):
    source = f"[{label}]({MEMBER})"
    original = source if field == "original" else "Synthetic original without links."
    req = SummarizeRequest(**{"title": "Fixture", **({field: source} if field != "original" else {})})
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER])]
    result = refine(client, config, original, req)
    print(json.dumps({"source": source, "field": field, "repair": repair,
                      "fixture_sha256": hashlib.sha256(source.encode()).hexdigest(),
                      "logical_calls": client.prompt.call_count, "artifacts": result.method_and_code.artifacts,
                      "receipt_exists": receipt_path.exists(), "actual_model_http_calls": 0}))
    assert client.prompt.call_count == (2 if repair else 1)
    assert result.method_and_code.artifacts == [MEMBER]
    assert not receipt_path.exists()


@pytest.mark.parametrize("source,candidate", [
    (f"[nested [code/repo]:]({MEMBER}_(v1))", MEMBER + "_(v1)"),
    (f"[Code:]({MEMBER}.)", MEMBER + "."),
    (f"[code/repo](<{MEMBER}.,>)", MEMBER + ".,"),
    (f"[Code:](HTTPS://EXAMPLE.org/Project%2F?q=a.#ID,)", "HTTPS://EXAMPLE.org/Project%2F?q=a.#ID,"),
    (f"[code]({MEMBER})", MEMBER),
    (f"<{MEMBER}>", MEMBER),
    (f"source_uri={MEMBER}", MEMBER),
    (f"[Code:]({MEMBER}) <{MEMBER}>", MEMBER),
])
def test_destination_scope_literal_identity_and_controls(source, candidate):
    assert _method_admission._source_uris(source) == {candidate}


@pytest.mark.parametrize("source", [
    f"ftp://mirror.example/?payload=[Code:]({MEMBER})",
    f"opaque+v1-resource:[code/repo]({MEMBER})",
    f"mailto:fixture@example.org?body=[Code:]({MEMBER})",
    f"9ftp://mirror.example/?payload=[code/repo]({MEMBER})",
    f"https:/mirror.example/?payload=[Code:]({MEMBER})",
    f"//mirror.example/?payload=[code/repo]({MEMBER})",
])
@pytest.mark.parametrize("field", ["original", "title", "doi", "abstract"])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_fake_destination_inside_containing_token(config, receipt_path, source, field, repair):
    original = source if field == "original" else "Synthetic original without links."
    req = SummarizeRequest(**{"title": "Fixture", **({field: source} if field != "original" else {})})
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER])]
    assert refine(client, config, original, req).method_and_code.artifacts == []
    assert client.prompt.call_count == (2 if repair else 1)
    assert rows(receipt_path)[0]["phase"] == ("repair" if repair else "first")


@pytest.mark.parametrize("label", ["code/repo", "Code:", "don't:", "owner's:'", '"opaque:', "<opaque:", "`opaque:", f"outer [don't:]({MEMBER})", f"don't:]({MEMBER}) [can't:", "\\[literal\\] don't:"])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_dry_valid_destination_remains_write_free(monkeypatch, config, label, repair):
    def forbidden(*args, **kwargs):
        raise AssertionError("Valid dry admission must not access Settings/state/writer")

    monkeypatch.setattr(_method_admission, "settings", forbidden)
    monkeypatch.setattr(_method_admission.run_log, "append_run", forbidden)
    monkeypatch.setattr(summarization, "state", forbidden)
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER])]
    client.pydantic_prompt.return_value = TriageResult(score=3, rationale="Fixture")
    result = summarization.run_abstract_dry(config, SummarizeRequest(title="Fixture", abstract=f"[{label}]({MEMBER})"), client)
    assert result.method_and_code.artifacts == [MEMBER]
    assert client.prompt.call_count == (2 if repair else 1)
    client.pydantic_prompt.assert_called_once()


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_complete_colon_free_raw_map_tail(monkeypatch, config, receipt_path, repair):
    from zotero_summarizer.services.library._map_reduce import split_chunks

    source = "A" * 80001 + f"\n[Code:]({MEMBER})"
    chunks = split_chunks(source, config.quality_review.map_chunk_chars)
    client = Mock()
    client.prompt.side_effect = ["Generated note without links."] * len(chunks) + (
        ["{malformed"] if repair else []) + [payload([MEMBER])]
    wire_pipeline(monkeypatch, config, client, source)
    result = summarization.run_pipeline(SummarizeRequest(title="Fixture", pdf_path="/synthetic.pdf"))
    assert result.method_and_code.artifacts == [MEMBER]
    assert client.prompt.call_count == len(chunks) + (2 if repair else 1)
    assert __import__("html").unescape(client.prompt.call_args_list[len(chunks)].args[0].split(
        "[Original URI context]\n", 1)[1].split("<untrusted_input>", 1)[1].split(
        "</untrusted_input>", 1)[0]) == source[source.index(MEMBER) - 200:]
    client.pydantic_prompt.assert_called_once()
    assert not receipt_path.exists()


class CountedText(str):
    """Count Python character/slice traversal, including views; not C regex work."""
    def __new__(cls, value, counter):
        instance = super().__new__(cls, value)
        instance.counter = counter
        return instance

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.counter[0] += len(value) if isinstance(key, slice) else 1
        return CountedText(value, self.counter) if isinstance(key, slice) else value

    def __iter__(self):
        for char in super().__iter__():
            self.counter[0] += 1
            yield char


@pytest.mark.parametrize("size", [1024, 2048, 4096])
@pytest.mark.parametrize("prefix", ["A", "[", "opaque:x ", "<opaque:"])
def test_instrumented_traversal_work_bound(size, prefix):
    source = prefix * size + " " + MEMBER
    counter = [0]
    assert _method_admission._source_uris(CountedText(source, counter)) == {MEMBER}
    print(json.dumps({"work_fixture_sha256": hashlib.sha256(source.encode()).hexdigest(),
                      "prefix": prefix, "chars": len(source), "python_char_slice_units": counter[0]}))
    assert counter[0] <= 12 * len(source)


DESTINATION = "https://example.org/other"
SECOND = "HTTPS://Tools.example.net/Release%2F?q=a.#ID,"
LABEL_AUTHORITY = [
    pytest.param(f"[repo {MEMBER} ]({DESTINATION})", [MEMBER, DESTINATION], id="parent-independent-label"),
    pytest.param(f"[{MEMBER}]({DESTINATION})", [MEMBER, DESTINATION], id="bounded-label-end"),
    pytest.param(f"[arbitrary {MEMBER} {SECOND} ]({DESTINATION})", [MEMBER, SECOND, DESTINATION], id="multiple"),
    pytest.param(f"[研究 / выбор {MEMBER} ]({DESTINATION})", [MEMBER, DESTINATION], id="unicode"),
    pytest.param(f"[outer [inner {MEMBER} ]({SECOND}) ]({DESTINATION})", [MEMBER, SECOND, DESTINATION], id="nested-link"),
    pytest.param(f"[outer [plain {MEMBER} ] child: {SECOND} ]({DESTINATION})", [MEMBER, SECOND, DESTINATION], id="nested-plain"),
    pytest.param(f"[escaped \\[literal\\] {MEMBER} ]({DESTINATION})", [MEMBER, DESTINATION], id="escaped-brackets"),
    pytest.param(f"[Code: <{MEMBER}.> ]({DESTINATION})", [MEMBER + ".", DESTINATION], id="literal-wrapper"),
]


@pytest.mark.parametrize("source,expected", LABEL_AUTHORITY)
@pytest.mark.parametrize("field", ["original", "title", "doi", "abstract"])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_label_authority_first_repair_metadata(config, receipt_path, source, expected, field, repair):
    original = source if field == "original" else "Synthetic original without links."
    req = SummarizeRequest(**{"title": "Fixture", **({field: source} if field != "original" else {})})
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload(expected)]
    result = refine(client, config, original, req)
    print(json.dumps({"label_source": source, "field": field, "repair": repair,
                      "fixture_sha256": hashlib.sha256(source.encode()).hexdigest(),
                      "logical_calls": client.prompt.call_count, "artifacts": result.method_and_code.artifacts,
                      "receipt_exists": receipt_path.exists(), "actual_model_http_calls": 0}))
    assert client.prompt.call_count == (2 if repair else 1)
    assert result.method_and_code.artifacts == expected
    assert not receipt_path.exists()


@pytest.mark.parametrize("field", ["title", "doi", "abstract"])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_label_authority_dry_write_free(monkeypatch, config, field, repair):
    def forbidden(*args, **kwargs):
        raise AssertionError("Dry label authority must not access Settings/state/writer")

    monkeypatch.setattr(_method_admission, "settings", forbidden)
    monkeypatch.setattr(_method_admission.run_log, "append_run", forbidden)
    monkeypatch.setattr(summarization, "state", forbidden)
    source = f"[repo {MEMBER} ]({DESTINATION})"
    req = SummarizeRequest(**{"title": "Fixture", "abstract": "Synthetic original without links.", field: source})
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER, DESTINATION])]
    client.pydantic_prompt.return_value = TriageResult(score=3, rationale="Fixture")
    result = summarization.run_abstract_dry(config, req, client)
    assert result.method_and_code.artifacts == [MEMBER, DESTINATION]
    assert client.prompt.call_count == (2 if repair else 1)
    client.pydantic_prompt.assert_called_once()


@pytest.mark.parametrize("containing", ["ftp://mirror.example/?x=", "custom+v1:payload=", "9ftp://mirror.example/?x=", "//mirror.example/?x="])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_label_authority_fake_scope_inside_token(config, receipt_path, containing, repair):
    fake = f"[repo {containing}[{MEMBER}]({SECOND}) ]({DESTINATION})"
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER, SECOND, DESTINATION])]
    assert refine(client, config, fake).method_and_code.artifacts == [DESTINATION]
    assert client.prompt.call_count == (2 if repair else 1)
    recorded = rows(receipt_path)
    assert [r["candidate"] for r in recorded] == [MEMBER, SECOND]
    assert [r["field"] for r in recorded] == ["method_and_code.artifacts[0]", "method_and_code.artifacts[1]"]
    assert all(r["phase"] == ("repair" if repair else "first") for r in recorded)
    assert all(r["original_text_sha256"] == hashlib.sha256(fake.encode()).hexdigest() for r in recorded)


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_label_authority_complete_raw_map(monkeypatch, config, receipt_path, repair):
    from zotero_summarizer.services.library._map_reduce import split_chunks

    source = "A" * 80001 + f"\n[repo {MEMBER} ]({DESTINATION})"
    chunks = split_chunks(source, config.quality_review.map_chunk_chars)
    client = Mock()
    client.prompt.side_effect = ["Generated note without links."] * len(chunks) + (
        ["{malformed"] if repair else []) + [payload([MEMBER, DESTINATION])]
    wire_pipeline(monkeypatch, config, client, source)
    result = summarization.run_pipeline(SummarizeRequest(title="Fixture", pdf_path="/synthetic.pdf"))
    assert result.method_and_code.artifacts == [MEMBER, DESTINATION]
    assert client.prompt.call_count == len(chunks) + (2 if repair else 1)
    assert __import__("html").unescape(client.prompt.call_args_list[len(chunks)].args[0].split(
        "[Original URI context]\n", 1)[1].split("<untrusted_input>", 1)[1].split(
        "</untrusted_input>", 1)[0]) == source[source.index(MEMBER) - 200:]
    client.pydantic_prompt.assert_called_once()
    assert not receipt_path.exists()


def _label_work_source(size, kind):
    if kind == "failed-nested":
        return "[" * size + " <opaque: " + MEMBER + " " + f"]({DESTINATION})" * size
    if kind == "failed-siblings":
        return f"[<opaque: {MEMBER} ]({DESTINATION}) " * size
    if kind == "nested":
        return "[" * size + " " + MEMBER + " " + f"]({DESTINATION})" * size
    if kind == "siblings":
        return f"[repo {MEMBER} ]({DESTINATION}) " * size
    return f"[\\[escaped\\] {MEMBER} ]({DESTINATION}) " * size


@pytest.mark.parametrize("size", [1024, 2048, 4096])
@pytest.mark.parametrize("kind", ["nested", "siblings", "escaped", "failed-nested", "failed-siblings"])
def test_label_authority_instrumented_work(size, kind):
    source = _label_work_source(size, kind)
    counter = [0]
    actual = _method_admission._source_uris(CountedText(source, counter))
    print(json.dumps({"label_work_kind": kind, "size": size, "chars": len(source),
                      "python_char_slice_units": counter[0], "actual": sorted(actual),
                      "fixture_sha256": hashlib.sha256(source.encode()).hexdigest()}))
    assert actual == {MEMBER, DESTINATION}
    assert counter[0] <= 12 * len(source)


HTTP_CONTAINER = f"HTTPS://Outer.example/Release%2F?q=]({MEMBER})#ID,."
CONTAINING_SCOPES = [
    pytest.param(f"[repo <ftp://mirror.example/?payload=]({MEMBER})> ]({DESTINATION})",
                 [MEMBER, DESTINATION], [DESTINATION], id="parent-angle-ftp"),
    pytest.param(f'[arbitrary "custom+v7:opaque=]({MEMBER})" ]({SECOND})',
                 [MEMBER, SECOND], [SECOND], id="double-opaque"),
    pytest.param(f"[研究 'data:text/plain,]({MEMBER})' ]({SECOND})",
                 [MEMBER, SECOND], [SECOND], id="single-data"),
    pytest.param(f"[tools `mailto:fixture@example.net?body=]({MEMBER})` ]({SECOND})",
                 [MEMBER, SECOND], [SECOND], id="backtick-mailto"),
    pytest.param(f"[outer [inner <opaque:x=]({MEMBER})]({DESTINATION})> ]({SECOND}) ]({DESTINATION})",
                 [MEMBER, SECOND, DESTINATION], [SECOND, DESTINATION], id="nested-fake-ends"),
    pytest.param(f"[a <custom:x=]({MEMBER})> ]({SECOND}) [b {DESTINATION} ]({SECOND})",
                 [MEMBER, SECOND, DESTINATION], [SECOND, DESTINATION], id="sibling-after-container"),
    pytest.param(f"[repo <{HTTP_CONTAINER}> ]({SECOND})",
                 [MEMBER, HTTP_CONTAINER, SECOND], [HTTP_CONTAINER, SECOND], id="http-outer-angle"),
    pytest.param(f'[repo "{HTTP_CONTAINER}" ]({SECOND})',
                 [MEMBER, HTTP_CONTAINER, SECOND], [HTTP_CONTAINER, SECOND], id="http-outer-quote"),
    pytest.param(f"[repo <opaque:x=[fake]({MEMBER})&y=]({DESTINATION})> ]({SECOND})",
                 [MEMBER, DESTINATION, SECOND], [SECOND], id="fake-open-and-end"),
    pytest.param(f"[repo <https:/broken.example?x=]({MEMBER})> ]({SECOND})",
                 [MEMBER, SECOND], [SECOND], id="malformed-containing"),
    pytest.param(f"[outer [inner](ftp://mirror.example/?x=]({MEMBER})) ]({SECOND})",
                 [MEMBER, SECOND], [SECOND], id="markdown-ftp"),
    pytest.param(f"[outer [inner](custom+v7:x=[fake]({MEMBER})&y=]({DESTINATION})) ]({SECOND})",
                 [MEMBER, DESTINATION, SECOND], [SECOND], id="markdown-opaque-fake-events"),
    pytest.param(f"[outer [inner]({HTTP_CONTAINER}) ]({SECOND})",
                 [MEMBER, HTTP_CONTAINER, SECOND], [HTTP_CONTAINER, SECOND], id="markdown-http-outer"),
    pytest.param(f"[outer [inner](data:text/plain,]({MEMBER})) ]({SECOND}) [\\[name\\] {DESTINATION} ]({SECOND})",
                 [MEMBER, SECOND, DESTINATION], [SECOND, DESTINATION], id="markdown-data-escaped-sibling"),
]


@pytest.mark.parametrize("source,candidates,expected", CONTAINING_SCOPES)
@pytest.mark.parametrize("field", ["original", "title", "doi", "abstract"])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_containing_scope_first_repair_metadata(config, receipt_path, source, candidates, expected, field, repair):
    original = source if field == "original" else "Synthetic original without links."
    req = SummarizeRequest(**{"title": "Fixture", **({field: source} if field != "original" else {})})
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload(candidates)]
    result = refine(client, config, original, req)
    recorded = rows(receipt_path) if receipt_path.exists() else []
    print(json.dumps({"containing_source": source, "field": field, "repair": repair,
                      "fixture_sha256": hashlib.sha256(source.encode()).hexdigest(),
                      "logical_calls": client.prompt.call_count, "artifacts": result.method_and_code.artifacts,
                      "receipts": recorded, "actual_model_http_calls": 0,
                      "admission_module": _method_admission.__file__, "refinement_module": summarization.__file__}))
    assert client.prompt.call_count == (2 if repair else 1)
    assert result.method_and_code.artifacts == expected
    rejected = [(i, uri) for i, uri in enumerate(candidates) if uri not in expected]
    assert [r["candidate"] for r in recorded] == [uri for _, uri in rejected]
    assert [r["field"] for r in recorded] == [f"method_and_code.artifacts[{i}]" for i, _ in rejected]
    metadata = {"title": req.title, "doi": req.doi, "abstract": req.abstract}
    metadata_sha = hashlib.sha256(json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode()).hexdigest()
    assert all(r["phase"] == ("repair" if repair else "first") and r["reason"] == "not_original_uri"
               and r["original_text_sha256"] == hashlib.sha256(original.encode()).hexdigest()
               and r["metadata_sha256"] == metadata_sha for r in recorded)


@pytest.mark.parametrize("field", ["original", "title", "doi", "abstract"])
@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_containing_scope_dry_rejects_before_io(monkeypatch, config, field, repair):
    forbidden = Mock(side_effect=AssertionError("Dry rejection cannot access Settings/state/writer"))
    monkeypatch.setattr(_method_admission, "settings", forbidden)
    monkeypatch.setattr(_method_admission.run_log, "append_run", forbidden)
    monkeypatch.setattr(summarization, "state", forbidden)
    source = f"[repo <custom:x=]({MEMBER})> ]({SECOND})"
    original = source if field == "original" else "Synthetic original without links."
    req = SummarizeRequest(**{"title": "Fixture", **({field: source} if field != "original" else {})})
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER])]
    with pytest.raises(ValueError, match="Write-free refinement rejected"):
        summarization._refine_with_retry(client, config, req, original, "containing-scope-dry",
                                        original_text=original, persist_rejections=False)
    assert client.prompt.call_count == (2 if repair else 1)
    forbidden.assert_not_called()


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_containing_scope_live_receipt_failure_propagates(monkeypatch, config, receipt_path, repair):
    writer = Mock(side_effect=OSError("Synthetic receipt failure"))
    monkeypatch.setattr(_method_admission.run_log, "append_run", writer)
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER, SECOND])]
    source = f"[repo <custom:x=]({MEMBER})> ]({SECOND})"
    with pytest.raises(OSError, match="Synthetic receipt failure"):
        refine(client, config, source)
    assert client.prompt.call_count == (2 if repair else 1)
    writer.assert_called_once()
    assert writer.call_args.args[0] == receipt_path
    assert writer.call_args.args[1]["candidate"] == MEMBER
    assert writer.call_args.args[1]["phase"] == ("repair" if repair else "first")


def _containing_work_source(size, kind):
    container = f"<custom:x=]({MEMBER})>"
    if kind == "markdown":
        return "[" * size + f"[inner](custom:x=]({MEMBER})) " + f"]({SECOND})" * size
    if kind == "nested":
        return "[" * size + " " + container + " " + f"]({SECOND})" * size
    if kind == "siblings":
        return f"[repo {container} ]({SECOND}) " * size
    return "[repo <opaque:" + (f"]({MEMBER})[fake" * size) + f"> ]({SECOND})"


@pytest.mark.parametrize("size", [1024, 2048, 4096])
@pytest.mark.parametrize("kind", ["nested", "siblings", "many-fake-ends", "markdown"])
def test_containing_scope_instrumented_work(size, kind):
    source = _containing_work_source(size, kind)
    counter = [0]
    actual = _method_admission._source_uris(CountedText(source, counter))
    print(json.dumps({"containing_work_kind": kind, "size": size, "chars": len(source),
                      "python_char_slice_units": counter[0], "actual": sorted(actual),
                      "fixture_sha256": hashlib.sha256(source.encode()).hexdigest()}))
    assert actual == {SECOND}
    assert counter[0] <= 12 * len(source)


@pytest.mark.parametrize("repair", [False, True], ids=["first", "repair"])
def test_markdown_containing_dry_before_io(monkeypatch, config, repair):
    forbidden = Mock(side_effect=AssertionError("Dry cannot access Settings/state/writer"))
    monkeypatch.setattr(_method_admission, "settings", forbidden)
    monkeypatch.setattr(_method_admission.run_log, "append_run", forbidden)
    monkeypatch.setattr(summarization, "state", forbidden)
    client = Mock()
    client.prompt.side_effect = (["{malformed"] if repair else []) + [payload([MEMBER])]
    client.pydantic_prompt.return_value = TriageResult(score=3, rationale="Fixture")
    req = SummarizeRequest(title="Fixture", abstract=f"[outer [inner](custom:x=]({MEMBER})) ]({SECOND})")
    with pytest.raises(ValueError, match="Write-free refinement rejected"):
        summarization.run_abstract_dry(config, req, client)
    assert client.prompt.call_count == (2 if repair else 1)
    client.pydantic_prompt.assert_not_called()
    forbidden.assert_not_called()


@pytest.mark.parametrize("source,expected", [
    (f"ftp://mirror.example/?x=]({MEMBER})", set()),
    (f"\\[inner\\](custom:x=]({MEMBER}))", set()),
    (f"[outer [inner]({MEMBER}) ]({SECOND})", {MEMBER, SECOND}),
    (f"<{MEMBER}", set()),
    (f"prefix'{MEMBER}'", set()),
    (f"prefix='{MEMBER}'", {MEMBER}),
    (f"prefix '{MEMBER}'", {MEMBER}),
    (f"[<{MEMBER}]({DESTINATION})", {DESTINATION}),
    (f'["{MEMBER}]({DESTINATION})', {DESTINATION}),
    (f"[`{MEMBER}]({DESTINATION})", {DESTINATION}),
    (f"[outer [inner]({MEMBER}?q=(x ]({SECOND})", {SECOND}),
])
def test_markdown_containing_counter_controls(source, expected):
    assert _method_admission._source_uris(source) == expected
