"""One cleaned paper body for every stage that judges the paper (#136).

Owner: the LLM stages read the WHOLE paper with only the reference list (and boilerplate)
removed. ``extract_body`` extracts once (the review's injected extractor, the one the digest,
verifier and receipt already bind; the fitz parser only supplies section layout) and ``clean``
strips only what is not the paper's argument: reference lists, acknowledgements / funding /
conflict / licence boilerplate and running headers + page numbers. Appendices stay.
``text_cap`` is the character window the body must fit; only above it do digest and
rubric fall back to selection / map_reduce, visibly (``long_paper_note``).
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

LOGGER = logging.getLogger(__name__)

# pdftotext prose is ~2.5-3 chars/token; 3 keeps the window conservative on the real text.
_CHARS_PER_TOKEN = 3
# Instructions, goals, claims, repair/retry suffixes sharing the window with the body.
_PROMPT_RESERVE_TOKENS = 12_000

_REF_HEADING = re.compile(r"^[ \t]*(?:\d+\.?[ \t]*)?(?:references|bibliography)[ \t]*$", re.I | re.M)
_BOILERPLATE_HEADING = re.compile(
    r"^[ \t]*(?:\d+\.?[ \t]*)?(?:acknowledg(?:e)?ments?|funding|conflicts? of interests?|competing interests?|"
    r"declaration of (?:competing )?interests?|disclosure statements?)[ \t]*$", re.I | re.M)
_LICENCE_LINE = re.compile(
    r"^[ \t]*(?:©|copyright\b|licensed under\b|this work is licensed\b|creative commons\b|"
    r"permission to make digital\b)", re.I)
_APPENDIX_HEADING = re.compile(r"^[ \t]*(?:appendix|appendices|supplementary|supplemental)\b", re.I)
_LONE_LETTER = re.compile(r"^[ \t]*[A-Z][ \t]*$")
_CITATION = re.compile(
    r"\b(?:19|20)\d\d\b|arXiv|doi|https?://|^\s*\[\d+\]|\bpp?\.|et al\.|Proceedings|Journal|Conference|Press\b", re.I)
_PAGE_NUMBER = re.compile(r"^[ \t]*\d{1,3}[ \t]*$")
_REF_WINDOW = 12            # consecutive non-empty lines without a citation cue end a reference list
_BOILERPLATE_CAP = 1_500    # a boilerplate block never eats more than this
_MIN_REF_POSITION = 0.2     # a References heading in the first fifth is a table of contents, not the list


def _reference_end(text: str, start: int) -> int:
    """End offset of the reference list that begins at the heading line ``start``.

    The list ends at an explicit appendix heading (``Appendix``, a lone ``A`` before a title);
    otherwise where ``_REF_WINDOW`` non-empty lines in a row carry no citation cue; else the end."""
    lines = text[start:].split("\n")
    offsets, pos = [], start
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    filled = [i for i, line in enumerate(lines) if line.strip()]
    for n, i in enumerate(filled[1:], 1):
        lone_title = _LONE_LETTER.match(lines[i]) and n + 1 < len(filled) and filled[n + 1] == i + 1
        if _APPENDIX_HEADING.match(lines[i]) or lone_title:
            return offsets[i]
    cues = [bool(_CITATION.search(lines[i])) for i in filled]
    for n in range(1, len(filled)):
        if not any(cues[n:n + _REF_WINDOW]):
            return offsets[filled[n]]
    return len(text)


def _strip_references(text: str) -> tuple[str, int]:
    """Cut every reference list (last first, so earlier offsets stay valid); appendices stay."""
    removed = 0
    starts = [m.start() for m in _REF_HEADING.finditer(text) if m.start() > _MIN_REF_POSITION * len(text)]
    for start in reversed(starts):
        end = _reference_end(text, start)
        text, removed = text[:start].rstrip("\n") + "\n\n" + text[end:].lstrip("\n"), removed + end - start
    return text, removed


def _boilerplate_end(text: str, start: int) -> int:
    """End of the boilerplate block whose heading line starts at ``start``: first blank line,
    reference/appendix heading or short heading-like line after a sentence end (capped)."""
    lines = text[start:start + _BOILERPLATE_CAP + 400].split("\n")
    pos = len(lines[0]) + 1
    for k in range(1, len(lines)):
        line, prev = lines[k], lines[k - 1]
        heading_like = (len(line) < 70 and line.strip() and line.strip()[0].isalnum() and not line.rstrip().endswith((".", ",", ";"))
                        and prev.rstrip().endswith("."))
        if (not line.strip() or _REF_HEADING.match(line) or _APPENDIX_HEADING.match(line) or heading_like
                or pos > _BOILERPLATE_CAP):
            break
        pos += len(line) + 1
    return start + pos


def _strip_boilerplate(text: str) -> tuple[str, int]:
    removed = 0
    while (m := _BOILERPLATE_HEADING.search(text)) is not None:
        end = _boilerplate_end(text, m.start())
        text, removed = text[:m.start()] + text[end:], removed + end - m.start()
    lines, kept = text.split("\n"), []
    skip = 0
    for line in lines:
        if skip or _LICENCE_LINE.match(line):
            removed += len(line) + 1
            skip = 0 if line.rstrip().endswith(".") else (skip or 3) - 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def _strip_running_headers(text: str) -> tuple[str, int]:
    """Drop lines repeated >=4 times that only ever stand at a page edge, and bare page numbers
    at a page edge (pages are joined by a blank line)."""
    pages = text.split("\n\n")
    edge = Counter()
    for page in pages:
        lines = [x for x in page.split("\n") if x.strip()]
        for line in {lines[0].strip(), lines[-1].strip()} if lines else ():
            edge[line] += 1
    total = Counter(x.strip() for x in text.split("\n") if x.strip())
    headers = {line for line, n in edge.items()
               if n >= 4 and total[line] == n and 12 <= len(line) <= 120 and not line.endswith(".")}
    removed, out = 0, []
    for page in pages:
        lines = page.split("\n")
        idx = [i for i, x in enumerate(lines) if x.strip()]
        drop = set()
        for i in (idx[:1] + idx[-1:]):
            if lines[i].strip() in headers or (_PAGE_NUMBER.match(lines[i]) and len(idx) > 1):
                drop.add(i)
        removed += sum(len(lines[i]) + 1 for i in drop)
        out.append("\n".join(x for i, x in enumerate(lines) if i not in drop))
    return "\n\n".join(out), removed


def _tidy(text: str) -> str:
    text = text.replace("\x0c", "\n").replace("\u00ad", "")
    text = re.sub(r"-\n(?=[a-z])", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean(raw: str) -> tuple[str, dict[str, int]]:
    """``(body, stats)``: hyphen/whitespace cleanup, then references, boilerplate, headers out."""
    text = _tidy(raw)
    text, headers = _strip_running_headers(text)
    text, references = _strip_references(text)
    text, boilerplate = _strip_boilerplate(text)
    body = _tidy(text)
    return body, {"extracted": len(raw), "body": len(body), "removed": len(raw) - len(body),
                  "references": references, "boilerplate": boilerplate, "headers": headers}


def extract_body(extractor: Any, pdf_path: str | Path) -> tuple[str, dict[str, int]]:
    """``(body, stats)`` from the review's one extractor; logs extracted - removed chars."""
    raw = extractor.extract_text(pdf_path)
    body, stats = clean(raw)
    LOGGER.info("paper body %s: %d chars extracted - %d removed (refs %d, boilerplate %d, headers %d) = %d",
                Path(pdf_path).name, stats["extracted"], stats["removed"], stats["references"],
                stats["boilerplate"], stats["headers"], stats["body"])
    return body, stats


class TextCap(NamedTuple):
    chars: int   # characters of body each stage may read
    whole: bool  # True: the whole body is the contract (remote); above ``chars`` the digest maps-reduces


def text_cap(qr: Any, provider: Any, *, lean_tier: bool) -> TextCap:
    """Lean and local tiers keep their configured caps (selection above them, as before); a remote
    provider gets its context window minus the prompt and output reserves."""
    if lean_tier:
        return TextCap(int(qr.lean_max_text_chars), False)
    if getattr(provider, "is_local", False):
        return TextCap(int(qr.max_text_chars), False)
    output = int(getattr(provider, "max_tokens", 0) or 0)
    tokens = int(qr.context_window_tokens) - _PROMPT_RESERVE_TOKENS - output
    return TextCap(max(int(qr.max_text_chars), tokens * _CHARS_PER_TOKEN), True)


def long_paper_note(chars: int, cap: int, chunk_chars: int) -> str:
    """Visible note when the body exceeds the window (empty otherwise)."""
    if chars <= cap:
        return ""
    parts = -(-chars // max(1, chunk_chars - 200))
    return f"Long paper: read in {parts} parts ({chars:,} chars exceed the {cap:,}-char window)."


def read_body(extractor: Any, pdf_path: str | Path, config: Any, lean_tier: bool) -> tuple[str, dict[str, Any]]:
    """``(body, window)`` for one review: the cleaned body plus its stats, the per-stage char cap
    (the deep-review provider's ``text_cap``) and the visible long-paper note."""
    from zotero_summarizer.models.providers import resolve_stage

    qr = config.quality_review
    body, stats = extract_body(extractor, pdf_path)
    cap = text_cap(qr, resolve_stage(config.llm_routing, "deep_review").provider, lean_tier=lean_tier)
    chars, whole = cap.chars, cap.whole
    note = long_paper_note(len(body), chars, qr.map_chunk_chars) if whole else ""
    if note:
        LOGGER.info("%s", note)
    return body, {**stats, "cap": chars, "whole_body": whole, "note": note}
