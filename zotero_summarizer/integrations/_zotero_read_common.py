"""Shared types/helpers for the Zotero reader (leaf module)."""
from __future__ import annotations

import re


class ZoteroReadError(RuntimeError):
    """Raised when reading from the local Zotero database fails."""


# Item types that are NOT bibliographic references — PDF attachments, standalone
# notes, and PDF annotations (a highlight is not a paper). Every "library items"
# query excludes these so annotations/attachments never appear as items or inflate
# counts. Single source of truth so the ~6 reader queries can't drift; inject as
# ``... typeName NOT IN ({_NON_BIBLIOGRAPHIC_TYPES_SQL}) ...`` (alias stays inline).
_NON_BIBLIOGRAPHIC_TYPES_SQL = "'attachment', 'note', 'annotation'"


# Strip C0/C1 control chars (preserving tab/newline/cr) plus Unicode tag chars
# (U+E0000-U+E007F). The tag-char range was infamously used to smuggle invisible
# prompt-injection payloads in 2024 — see Greshake et al. USENIX Security 2024
# (arXiv:2302.12173v3) and Anthropic's Dec 2024 indirect-prompt-injection guidance.
# All feed-supplied strings pass through this on read so the rest of the pipeline
# cannot accidentally hand untrusted control chars to an LLM.
_INJECTION_CHAR_PATTERN = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\U000e0000-\U000e007f]"
)


_ARXIV_RE = re.compile(
    r"(?:arxiv[.:/]|arxiv\.org/(?:abs|pdf)/)([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})",
    re.IGNORECASE,
)

# Zotero stores annotation kind as an integer; map to the canonical string label
# used by the JS client. Source: Zotero/chrome/content/zotero/xpcom/annotations.js
_ANNOTATION_TYPE_NAMES: dict[int, str] = {
    1: "highlight",
    2: "note",
    3: "image",
    4: "ink",
    5: "underline",
    6: "text",
}


def _arxiv_id_from_url_or_doi(url: str, doi: str) -> str:
    """Extract an arXiv ID from a feed item's URL or DOI fields."""
    for value in (url, doi):
        if not value:
            continue
        match = _ARXIV_RE.search(value)
        if match:
            return match.group(1)
    return ""
