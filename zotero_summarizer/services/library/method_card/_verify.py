"""Keep only what the paper itself supports.

Every shown quote must be a strict verbatim span of the paper body (``quote_is_grounded``,
``fuzzy=False``) and the claim written next to it must follow from that quote
(``answer_is_supported_by_quote``). A compared row additionally needs its numbers inside the quote.
A row that fails is dropped, never edited; a core idea that fails makes the whole card fail."""
from __future__ import annotations

import re
from typing import Any

from zotero_summarizer.services.library._grounding import answer_is_supported_by_quote, quote_is_grounded
from zotero_summarizer.services.library._review_section_overlay import _SENTINEL_TITLE_RE, _locate_quote_kind
from zotero_summarizer.services.library.method_card._prompt import CardResponse

MAX_COMPARED = 4
MAX_REUSE = 3
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_REFERENCE_TITLE_RE = re.compile(r"^\W*(?:references?|bibliography|works cited)\W*$", re.IGNORECASE)


class CardNotGrounded(ValueError):
    """The core idea could not be tied to a quote in the paper: no (partial) card is shown."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUMBER_RE.findall(text)}


def _section_label(quote: str, sections: list[dict[str, Any]]) -> str:
    """``§ Title`` (or ``p.N`` when the heading is a page-fallback sentinel); '' when no section holds it."""
    located, _kind = _locate_quote_kind(quote, sections)
    if not located:
        return ""
    title = _clean(located.get("title"))
    if not title or _SENTINEL_TITLE_RE.match(title):
        return f"p.{located['page']}" if located.get("page") else ""
    return f"§ {title}"


def _supported(claim: str, quote: str, body: str) -> bool:
    return bool(claim) and quote_is_grounded(quote, body, fuzzy=False) and answer_is_supported_by_quote(claim, quote)


def _words(text: str) -> int:
    return len(text.split())


def _short_text(core: str, compared: list[dict[str, str]], use_when: str) -> str:
    """≈100-word level, assembled from verified parts only (never a second model paraphrase)."""
    parts = [core]
    if compared:
        parts.append(f"{compared[0]['what']}: {compared[0]['result']}.")
    if use_when:
        parts.append(f"Use it when {use_when.rstrip('.')}.")
    return " ".join(parts)


def verify_card(raw: CardResponse, sections: list[dict[str, Any]], body: str) -> dict[str, Any]:
    """Verified card fields; raises ``CardNotGrounded`` when the core idea is not supported."""
    body_sections = [s for s in sections if not _REFERENCE_TITLE_RE.match(str(s.get("title") or ""))]

    def where(quote: str) -> str:
        return _section_label(quote, body_sections)

    core_text, core_quote = _clean(raw.core_idea.text), _clean(raw.core_idea.quote)
    if not _supported(core_text, core_quote, body):
        raise CardNotGrounded("core idea is not supported by a quote found in the paper")
    compared = []
    for row in raw.compared:
        what, result, quote = _clean(row.what), _clean(row.result), _clean(row.quote)
        numbers = _numbers(result)
        if (numbers and numbers <= _numbers(quote) and _supported(what, quote, body)
                and answer_is_supported_by_quote(result, quote)):
            compared.append({"what": what, "result": result, "quote": quote, "section": where(quote)})
    reuse = []
    for step in raw.reuse:
        quote, operation = _clean(step.quote), _clean(step.operation)
        if _clean(step.input) and _clean(step.output) and _supported(operation, quote, body):
            reuse.append({"input": _clean(step.input), "operation": operation, "output": _clean(step.output),
                          "quote": quote, "section": where(quote)})
    claim = _clean(raw.novelty_claim.quote)
    novelty = ({"claim_quote": claim, "section": where(claim), "check_state": "not_run", "checks": []}
               if quote_is_grounded(claim, body, fuzzy=False) else None)
    caveat_text, caveat_quote = _clean(raw.caveat.text), _clean(raw.caveat.quote)
    caveat = ({"text": caveat_text, "quote": caveat_quote, "section": where(caveat_quote)}
              if _supported(caveat_text, caveat_quote, body) else None)
    use_when = _clean(raw.use_when) if _words(_clean(raw.use_when)) <= 40 else ""
    compared = compared[:MAX_COMPARED]
    return {
        "core_idea": {"text": core_text, "quote": core_quote, "section": where(core_quote)},
        "compared": compared, "novelty": novelty, "reuse": reuse[:MAX_REUSE], "use_when": use_when,
        "caveat": caveat, "short_text": _short_text(core_text, compared, use_when),
    }
