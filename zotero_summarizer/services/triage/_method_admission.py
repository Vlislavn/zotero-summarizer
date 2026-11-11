"""Original-source machine URI admission, not contribution-role verification."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from string import ascii_letters, digits
from threading import Lock
from urllib.parse import urlsplit

from zotero_summarizer.integrations.pdf import PdfSource
from zotero_summarizer.models import RefinedSummary, SummarizeRequest
from zotero_summarizer.services import run_log
from zotero_summarizer.services._common import settings

_SCHEME_CHARS = frozenset(ascii_letters + digits + "+.-")
_IDENTIFIER_CHARS = frozenset(ascii_letters + digits + "_.-")
_RECEIPT_LOCK = Lock()
_LEXICAL_GRAMMAR = "complete-http-v7: complete scoped tokens precede label events; bounded label and destination scopes; wrappers literal; bare single prose dot/comma"


def _bare_uri(token: str) -> str:
    stack = []
    unmatched = set()
    pairs = {")": "(", "]": "[", "}": "{"}
    for index, char in enumerate(token):
        if char in "([{":
            stack.append(char)
        elif char in pairs:
            if stack and stack[-1] == pairs[char]:
                stack.pop()
            else:
                unmatched.add(index)
    end = len(token)
    while end - 1 in unmatched:
        end -= 1
    if ("?" not in token and "#" not in token and token[end - 1:end] in {".", ","}
            and token[end - 2:end - 1] not in ".,;:!?"):
        end -= 1
    while end - 1 in unmatched:
        end -= 1
    return token[:end]


def _scheme_end(text: str, cursor: int) -> int:
    while cursor < len(text) and text[cursor] in _SCHEME_CHARS:
        cursor += 1
    return cursor


def _uri_closer(text: str, start: int) -> str:
    before = text[start - 1] if start else ""
    if before in {"\"", "'", "`"} and start > 1 and (text[start - 2].isalnum() or text[start - 2] in _IDENTIFIER_CHARS):
        return ""
    return ">" if before == "<" else before if before in {"\"", "'", "`"} else ""


def _explicit_ends(text: str) -> dict[int, int]:
    ends, following, parens = {}, {}, []
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char.isspace():
            following, parens = {}, []
        if char == ")":
            parens.append(index)
        elif char == "(" and parens:
            ends[index] = parens.pop()
        closer = _uri_closer(text, index + 1)
        if closer in following:
            ends[index] = following[closer]
        if char in ">\"'`":
            following[char] = index
    return ends


def _markdown_labels(text: str, ends: dict[int, int]) -> dict[int, int]:
    """Index label ends outside explicit tokens, before visiting label contents."""
    labels, stack = {}, []
    index, markdown_start, escaped = 0, -1, False
    while index < len(text):
        char = text[index]
        index += 1
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in _SCHEME_CHARS:
            start = index - 1
            index = _scheme_end(text, start)
            end = ends.get(start - 1) if start == markdown_start or text[start - 1:start] != "(" else None
            if index < len(text) and text[index] == ":" and end is not None:
                index = end + 1
            continue
        if char == "[":
            stack.append(index - 1)
        elif char == "]" and stack:
            opening = stack.pop()
            if text[index:index + 1] == "(":
                labels[opening] = index - 1
                markdown_start = index + 1
    return labels


def _uri_end(text: str, cursor: int, closer: str, markdown: bool, *, limit: int) -> int:
    depth = 0
    while cursor < limit and not text[cursor].isspace():
        char = text[cursor]
        if closer and char == closer:
            break
        if markdown:
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
        cursor += 1
    return cursor


def _source_uri_spans(text: str) -> Iterator[tuple[int, int, str]]:
    ends = _explicit_ends(text)
    label_ends = _markdown_labels(text, ends)
    active_labels = []
    cursor, markdown_start, identifier = 0, -1, 0  # identifier: empty/valid/invalid
    blocked, boundary = False, True
    while cursor < len(text):
        if active_labels and cursor == active_labels[-1]:
            cursor = markdown_start = active_labels.pop() + 2
            blocked, boundary, identifier = False, True, 0
            continue
        char = text[cursor]
        if char.isspace():
            blocked, boundary, identifier = False, True, 0
            cursor += 1
            continue
        if char == "[" and cursor in label_ends and not blocked:
            active_labels.append(label_ends[cursor])
            cursor += 1
            boundary, identifier = True, 0
            continue
        if char not in _SCHEME_CHARS:
            opener = char in "<([{" or char in "\"'`" and bool(_uri_closer(text, cursor + 1))
            boundary = not blocked and (opener or char == "=" and identifier == 1)
            blocked |= char in ":/?#"
            identifier = 1 if char == "_" and identifier != 2 else 2
            cursor += 1
            continue
        start = cursor
        cursor = _scheme_end(text, cursor)
        word = text[start:cursor]
        if cursor == len(text) or text[cursor] != ":":
            if identifier == 0:
                identifier = 1 if word[0] in ascii_letters else 2
            if any(char not in _IDENTIFIER_CHARS for char in word):
                identifier = 2
            boundary = False
            continue
        admissible = (boundary and not blocked and word.lower() in {"http", "https"}
                      and text[cursor + 1:cursor + 3] == "//")
        closer = _uri_closer(text, start)
        markdown = start == markdown_start
        end = ends.get(start - 1) if closer or markdown else None
        limit = active_labels[-1] if active_labels and end is None else len(text)
        end = _uri_end(text, cursor + 1, closer, markdown, limit=limit) if end is None else end
        token = text[start:end]
        if closer or markdown:
            if admissible and end < len(text) and text[end] == (closer or ")"):
                yield start, start + len(token), token
        elif admissible:
            uri = _bare_uri(token)
            yield start, start + len(uri), uri
        cursor = end + int(end < limit and not text[end].isspace())
        blocked, boundary, identifier = True, False, 2


def _source_uris(text: str) -> set[str]:
    return {uri for _, _, uri in _source_uri_spans(text)}


def _validate_pdf_authority(original_text: str, original_pdf: PdfSource | None) -> None:
    if original_pdf is not None:
        if not isinstance(original_pdf, PdfSource):
            raise TypeError("Original PDF authority must be a PdfSource")
        if original_text != original_pdf.text:
            raise ValueError("Original text conflicts with captured PDF source")


def _eligible_annotation(uri: str) -> bool:
    end = _scheme_end(uri, 0)
    if uri[:end].lower() not in {"http", "https"} or uri[end:end + 3] != "://":
        return False
    if any(not char.isprintable() or char.isspace() or char in '\"`<>' for char in uri):
        return False
    try:
        return bool(urlsplit(uri).hostname)
    except ValueError:
        return False


def _admit_method_artifacts(
    refined: RefinedSummary, original_text: str, req: SummarizeRequest, *, phase: str,
    persist_rejections: bool = True, original_pdf: PdfSource | None = None,
) -> RefinedSummary:
    _validate_pdf_authority(original_text, original_pdf)
    method = refined.method_and_code
    if method is None or not method.artifacts:
        return refined
    metadata = {"title": req.title, "doi": req.doi, "abstract": req.abstract}
    available = _source_uris(original_text)
    for value in metadata.values():
        if value:
            available.update(_source_uris(value))
    if original_pdf is not None:
        available.update(uri for uri in original_pdf.annotation_uris if _eligible_annotation(uri))
    rejected = [(index, uri) for index, uri in enumerate(method.artifacts) if uri not in available]
    if not rejected:
        return refined
    if not persist_rejections:
        raise ValueError("Write-free refinement rejected method artifact URI absent from original source")
    original_digest = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
    metadata_digest = hashlib.sha256(json.dumps(
        metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    pdf_identity = {} if original_pdf is None else {
        "original_document_sha256": original_pdf.document_sha256,
        "annotation_authority_sha256": hashlib.sha256(json.dumps(
            original_pdf.annotation_uris, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest(),
    }
    path = settings().data_dir / "method-artifact-rejections.jsonl"
    with _RECEIPT_LOCK:
        for index, uri in rejected:
            run_log.append_run(path, {
                "schema_version": 1, "original_text_sha256": original_digest,
                "metadata_sha256": metadata_digest, "phase": phase,
                "field": f"method_and_code.artifacts[{index}]", "candidate": uri,
                "reason": "not_original_uri", "lexical_grammar": _LEXICAL_GRAMMAR,
                **pdf_identity,
            })
    admitted = method.model_copy(update={"artifacts": [uri for uri in method.artifacts if uri in available]})
    return refined.model_copy(update={"method_and_code": admitted})
