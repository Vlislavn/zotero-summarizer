"""Pure metadata/text helpers for the paper-read artifact.

Split out of ``paper_render`` to keep that facade under the 500-LOC cap. These
functions have no ``Settings`` or filesystem-state dependency — they only shape
extracted/Zotero metadata into artifact fields and assemble the Q&A grounding
text. See ``services/library/README.md``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# TeX-extracted authors are often garbage (Author 1, Author 2… or bare \cmd residue).
_GARBAGE_AUTHOR_RE = re.compile(r"(?i)\bAuthor\s*\d+|\\[a-zA-Z]")
# Zotero storage folder names are 8-char uppercase alphanumeric keys — not a title.
_ZOTERO_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")


def _format_zotero_authors(raw: Any) -> str:
    """Flatten Zotero creator list (dicts or strings) into a single comma-joined string."""
    if not raw:
        return ""
    names = []
    for a in (raw if isinstance(raw, list) else [raw]):
        if isinstance(a, dict):
            name = " ".join(filter(None, [str(a.get("firstName") or ""), str(a.get("lastName") or "")])).strip()
            if not name:
                name = str(a.get("name") or "").strip()
        else:
            name = str(a).strip()
        if name:
            names.append(name)
    return ", ".join(names)


def _tags_as_keywords(tags: Any) -> list[str]:
    """Convert Zotero tags to a keyword list, filtering out internal zs: and emoji-only tags."""
    if not tags:
        return []
    result = []
    for t in (tags if isinstance(tags, list) else []):
        tag = str(t.get("tag", "") if isinstance(t, dict) else t).strip()
        if not tag or tag.startswith("zs:") or len(tag) > 60:
            continue
        if not any(c.isalpha() for c in tag):  # drop pure-symbol / emoji-only tags
            continue
        result.append(tag)
    return result[:8]


def apply_metadata_fallbacks(
    content: dict[str, Any], *, zotero_detail: dict[str, Any] | None, title: str, pdf_stem: str
) -> None:
    """In place: prefer Zotero metadata over garbage TeX extraction.

    P0/P2: Zotero authors/keywords win where TeX extraction is garbage or empty,
    and the Zotero title replaces a missing/stem/Zotero-key title.
    """
    if zotero_detail:
        if _GARBAGE_AUTHOR_RE.search(str(content.get("authors") or "")) or not content.get("authors"):
            zotero_authors = _format_zotero_authors(zotero_detail.get("authors"))
            if zotero_authors:
                content["authors"] = zotero_authors
        if not content.get("keywords"):
            zotero_kw = _tags_as_keywords(zotero_detail.get("tags"))
            if zotero_kw:
                content["keywords"] = zotero_kw
    if title and (
        not content.get("title")
        or content.get("title") == pdf_stem
        or _ZOTERO_KEY_RE.match(str(content.get("title") or ""))
    ):
        content["title"] = title


def qa_body_text(artifact: dict[str, Any]) -> str:
    """The paper's PDF-extracted body text used for grounding, all tiers.

    ``qa_text`` is the clean PDF extraction persisted for TeX papers (whose own
    ``full_text`` is noisy ``_clean_tex`` output); PDF-tier papers fall back to
    their ``full_text`` (same extraction)."""
    return str(artifact.get("qa_text") or artifact.get("full_text") or "")


def artifact_text(artifact: dict[str, Any], *, max_chars: int) -> str:
    """Comprehensive Q&A context: metadata, generated notes, then paper body."""
    parts = [
        f"Title: {artifact.get('title') or ''}",
        f"Pages: {artifact.get('n_pages') or 0}",
        f"Figures: {artifact.get('figures_count') or 0}",
        f"References: {artifact.get('references_count') or 0}",
    ]
    notes_path = Path(str((artifact.get("outputs") or {}).get("notes") or ""))
    if notes_path.is_file():
        parts.append(notes_path.read_text(encoding="utf-8"))
    body = qa_body_text(artifact)
    if body:
        parts.append(body)
    return "\n\n".join(parts)[:max_chars]
