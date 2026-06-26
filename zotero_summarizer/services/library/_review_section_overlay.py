"""Locate deep-review findings onto the paper's OWN sections.

Computed INSIDE the review run (``_deep_review_layers.extra_layers``) from the
same ``extract_pdf_content`` sections the quality eval + goal summaries were
grounded against — so a section id is a stable join key by construction, never a
substring match into a different extractor's text (the cross-extractor mismatch
that makes a read-time overlay unreliable). The result rides on the cached review
as ``section_overlay`` and is surfaced verbatim by ``review_detail``.

Pure assembly — NO extra LLM call. Locating is **two-tier** (the shipped pattern,
AI2 CiteRead, IUI'22): a precise grounded span match first (verbatim → fuzzy, the
SAME ``quote_is_grounded`` bar the review used), and a coarse SECTION-LEVEL
fallback (best lexical-overlap section) when no span grounds — so a finding NEVER
silently loses its anchor; it degrades to an ``approx`` section instead. Each
located finding carries a ``match`` kind (``exact`` | ``fuzzy`` | ``approx`` |
None) so the UI can render an approximate location conservatively (the brittle
critique/overstatement signal must not assert a location it isn't sure of) and so
``localization_stats`` can report the operating point.

Degrades to ``degraded=True`` (findings carried flat, no anchors) when the
sections are page-fallback sentinels (``Page N`` / ``Front matter``) or have no
body text (docling).
"""
from __future__ import annotations

import re
from typing import Any

from zotero_summarizer.services.library._grounding import quote_is_grounded

# Sentinel titles produced by the page fallback / preamble — not real headings.
_SENTINEL_TITLE_RE = re.compile(r"^(?:Front matter|Page\s+\d+)$", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Section-level fallback floors: the approximate section must share at least this
# FRACTION of the finding's content tokens AND this many tokens absolutely — high
# enough that a description with no real lexical tie to any section stays unplaced
# (the brittle case the research says must not assert a location). Named, not magic.
_APPROX_MIN_OVERLAP = 0.5
_APPROX_MIN_TOKENS = 4


def _norm_title(title: Any) -> str:
    return " ".join(str(title or "").split()).strip().lower()


def _label(section: dict[str, Any]) -> dict[str, Any]:
    """The light section reference carried on a located finding (no body text)."""
    return {"id": section.get("id"), "title": section.get("title"), "page": section.get("page")}


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


def _is_degraded(sections: list[dict[str, Any]]) -> bool:
    """True when no section has both a real (non-sentinel) title and body text — the
    two conditions a quote-to-section locate needs to be meaningful."""
    if not sections:
        return True
    has_real_title = any(
        not _SENTINEL_TITLE_RE.match(str(s.get("title") or "").strip()) for s in sections
    )
    has_body = any(str(s.get("text") or "").strip() for s in sections)
    return not (has_real_title and has_body)


def _locate_quote_kind(quote: Any, sections: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    """``(section_label, "exact"|"fuzzy")`` for the section whose body grounds
    ``quote`` (verbatim wins over fuzzy; first section wins on ties), else
    ``(None, None)``. Short quotes that can't clear the grounding floors → None."""
    text = str(quote or "")
    if not text.strip():
        return None, None
    for section in sections:
        if quote_is_grounded(text, str(section.get("text") or ""), fuzzy=False):
            return _label(section), "exact"
    for section in sections:
        if quote_is_grounded(text, str(section.get("text") or ""), fuzzy=True):
            return _label(section), "fuzzy"
    return None, None


def _best_section(quote: Any, sections: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The section with the largest lexical overlap with ``quote`` — the coarse
    SECTION-LEVEL fallback when no span grounds. Returns None (stays unplaced) when
    no section clears the conservative overlap floors, so a description with no real
    tie to the paper is never mislocated."""
    q = set(_tokens(quote))
    if len(q) < _APPROX_MIN_TOKENS:
        return None
    best: dict[str, Any] | None = None
    best_shared = 0
    for section in sections:
        shared = len(q & set(_tokens(section.get("text"))))
        if shared > best_shared:
            best_shared, best = shared, section
    if best is None or best_shared < _APPROX_MIN_TOKENS or best_shared / len(q) < _APPROX_MIN_OVERLAP:
        return None
    return _label(best)


def _locate_finding(text: Any, sections: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    """Two-tier locate for a single free-text finding: grounded span first, coarse
    best-section fallback (``approx``) second, else unplaced."""
    loc, kind = _locate_quote_kind(text, sections)
    if loc is not None:
        return loc, kind
    approx = _best_section(text, sections)
    return (approx, "approx") if approx is not None else (None, None)


def _locate_goal(
    goal: dict[str, Any],
    sections: list[dict[str, Any]],
    by_title: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sections for one goal: its ``key_sections`` titles resolved against the same
    sections list (exact, since both came from one extraction), with a
    supporting-quote grounded fallback when the titles don't resolve."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for title in goal.get("key_sections") or []:
        section = by_title.get(_norm_title(title))
        if section is not None and section.get("id") not in seen:
            seen.add(section.get("id"))
            out.append(_label(section))
    if not out:
        for quote in goal.get("supporting_quotes") or []:
            loc, _kind = _locate_quote_kind(quote, sections)
            if loc is not None and loc["id"] not in seen:
                seen.add(loc["id"])
                out.append(loc)
    return out


def build_section_overlay(
    sections: list[dict[str, Any]],
    quality: dict[str, Any] | None,
    goal_summaries: list[dict[str, Any]] | None,
    *,
    section_summaries: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assemble the overlay from the review's own data.

    Contract (index-aligned so the frontend joins by position to the same lists it
    already receives on ``deep_review``):

        {
          "sections":        [{id, title, page, summary}],                # the paper map
          "degraded":        bool,                                        # sentinels/empty
          "goals":           [{goal, sections:[{id,title,page}]}],        # ↔ goal_summaries
          "red_flags":       [{text, section:{...}|None, match}],         # ↔ quality.red_flags
          "missing_critical":[{item, section:{...}|None, match}],         # ↔ quality.missing_critical
        }

    ``match`` is ``exact`` | ``fuzzy`` | ``approx`` | None (None = unplaced).
    ``section_summaries`` (section_id -> one sentence) is the optional Phase-C
    enrichment merged onto the outline; "" when not run.
    """
    summaries = section_summaries or {}
    outline = [
        {
            "id": s.get("id"),
            "title": s.get("title"),
            "page": s.get("page"),
            "summary": summaries.get(str(s.get("id") or ""), ""),
        }
        for s in (sections or [])
    ]
    q = quality or {}
    goals_in = goal_summaries or []
    red_flags_in = list(q.get("red_flags") or [])
    missing_in = list(q.get("missing_critical") or [])

    if _is_degraded(sections or []):
        # Carry the finding TEXT so the UI still shows the problems/goals — just no
        # (mislabelled) section anchor.
        return {
            "sections": outline,
            "degraded": True,
            "goals": [{"goal": g.get("goal", ""), "sections": []} for g in goals_in],
            "red_flags": [{"text": t, "section": None, "match": None} for t in red_flags_in],
            "missing_critical": [{"item": t, "section": None, "match": None} for t in missing_in],
        }

    by_title = {_norm_title(s.get("title")): s for s in sections}
    evidence = q.get("evidence") or {}

    def _flag(text: Any) -> dict[str, Any]:
        loc, kind = _locate_finding(text, sections)
        return {"text": text, "section": loc, "match": kind}

    def _missing(item: Any) -> dict[str, Any]:
        # A critical item's own evidence quote locates best; fall back to the item
        # label's two-tier locate.
        loc, kind = _locate_quote_kind(evidence.get(item, ""), sections)
        if loc is None:
            loc, kind = _locate_finding(item, sections)
        return {"item": item, "section": loc, "match": kind}

    return {
        "sections": outline,
        "degraded": False,
        "goals": [
            {"goal": g.get("goal", ""), "sections": _locate_goal(g, sections, by_title)}
            for g in goals_in
        ],
        "red_flags": [_flag(t) for t in red_flags_in],
        "missing_critical": [_missing(item) for item in missing_in],
    }


def localization_stats(overlay: dict[str, Any] | None) -> dict[str, Any]:
    """Operating-point breakdown of how the red-flag / missing-critical findings
    were located — ``{exact, fuzzy, approx, unplaced, total, located_rate}`` — so the
    chip-confidence threshold can be CALIBRATED against the real review corpus
    (run over many cached reviews) rather than guessed. ``approx`` is the
    low-confidence section fallback the UI must render conservatively."""
    counts = {"exact": 0, "fuzzy": 0, "approx": 0, "unplaced": 0}
    if overlay and not overlay.get("degraded"):
        findings = list(overlay.get("red_flags") or []) + list(overlay.get("missing_critical") or [])
        for f in findings:
            counts[f.get("match") or "unplaced"] = counts.get(f.get("match") or "unplaced", 0) + 1
    total = sum(counts.values())
    located = counts["exact"] + counts["fuzzy"] + counts["approx"]
    return {**counts, "total": total, "located_rate": (located / total) if total else 0.0}


__all__ = ["build_section_overlay", "localization_stats"]
