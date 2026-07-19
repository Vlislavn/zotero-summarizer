"""Relevance band + "why it surfaced" chips for Targeted Search results.

Single responsibility: turn a candidate's already-computed ``query_score`` (our
cross-encoder, sigmoid ``[0,1]`` — see ``rank.py``) + the fields already on the
Candidate into (a) a 3-way relevance band and (b) a short ordered list of plain
chips for the card. Pure thresholds on numbers already present — no LLM, no I/O.

Mirrors ``daily_select/_relevance`` (the Today card's ``build_why``/``goal_bands``):
the band is the cohort's own **terciles** (self-calibrating across reranker/query
phrasings), guarded by an **absolute evidence floor** so a pool of all-bad hits
can't manufacture a "strong" group out of the least-bad third.
"""
from __future__ import annotations

import os
import re
from typing import Any

from zotero_summarizer.services.search._models import Candidate

# Absolute floors on the sigmoid [0,1] cross-encoder score: nothing is "strong"
# below _STRONG_FLOOR nor "on-topic" below _MILD_FLOOR, no matter the terciles — a
# safety net for the all-bad pool. Calibration knobs (env-overridable): the bge
# reranker sigmoid is roughly bimodal, so these are conservative, not tuned.
_STRONG_FLOOR = float(os.environ.get("ZS_SEARCH_STRONG_FLOOR", "0.30"))
_MILD_FLOOR = float(os.environ.get("ZS_SEARCH_MILD_FLOOR", "0.05"))
_MAX_WHY = 3  # keep the card scannable (Miller's Law)

# "ICLR 2025 Oral" -> "ICLR'25 Oral" (compact year, keep the tier word).
_VENUE_YEAR = re.compile(r"^(\S+)\s+(\d{4})\b\s*(.*)$")


def relevance_bands(candidates: list[Candidate]) -> tuple[float | None, float | None]:
    """``(strong, mild)`` band thresholds = the top- and middle-tercile lower edges
    of the cohort's PRESENT, POSITIVE ``query_score``s, each raised to at least its
    absolute floor. ``(None, None)`` when fewer than 3 candidates carry a positive
    score (a tercile needs 3 points; below that the split degenerates) — the caller
    then assigns no band, never a guessed one."""
    vals = sorted(c.query_score for c in candidates if c.query_score is not None and c.query_score > 0)
    if len(vals) < 3:
        return None, None
    strong = max(vals[(2 * len(vals)) // 3], _STRONG_FLOOR)
    mild = max(vals[len(vals) // 3], _MILD_FLOOR)
    return strong, mild


def band_for(score: float | None, strong: float | None, mild: float | None) -> str:
    """One candidate's band: ``strong`` / ``on_topic`` / ``weak`` / ``""`` (bands
    suppressed or no score)."""
    if score is None or strong is None or mild is None:
        return ""
    if score >= strong:
        return "strong"
    if score >= mild:
        return "on_topic"
    return "weak"


def _quality_chip(quality: dict[str, Any]) -> str:
    grade = str(quality.get("grade") or "").upper()
    if grade in {"A", "B"}:
        return f"Grade {grade}"
    if str(quality.get("quality_band") or "").lower() == "highlight":
        return "High quality"
    return ""


def _peer_review_chip(peer_review: dict[str, Any]) -> str:
    """Compact chip from the OpenReview signal bag (SIGNAL-ONLY, no ranking
    effect), e.g. ``"ICLR 2025 Oral"`` -> ``"ICLR'25 Oral"``. Falls back to the
    venue string verbatim, then the tier capitalized, when the venue doesn't
    parse into name+year+tier."""
    if not peer_review:
        return ""
    venue = str(peer_review.get("venue") or "").strip()
    m = _VENUE_YEAR.match(venue)
    if m:
        name, year, rest = m.groups()
        return f"{name}'{year[-2:]} {rest}".strip()
    if venue:
        return venue
    tier = str(peer_review.get("tier") or "").strip()
    return tier.capitalize() if tier else ""


def build_why(cand: Candidate, band: str) -> list[str]:
    """Ordered, capped reason chips for one card — strongest signal first. Missing
    signals are simply omitted (an empty list is a valid "nothing cleared a bar",
    not an error). Retraction is NOT chipped here: the card already shows a loud
    RETRACTED tag, so a chip would only duplicate it."""
    why: list[str] = []

    # 1. Query relevance — the master key (our cross-encoder vs the query).
    if band == "strong":
        why.append("Strong match")
    elif band == "on_topic":
        why.append("On-topic")

    # 2. Cross-source agreement — the same work found by >1 federation channel.
    sources = {p.source for p in cand.provenance if getattr(p, "source", "")}
    if len(sources) >= 2:
        why.append(f"In {len(sources)} sources")

    # 3. Quality (one chip: grade if graded, else the highlight band).
    chip = _quality_chip(cand.quality)
    if chip:
        why.append(chip)

    # 3.5. Peer-review tier (OpenReview signal — SIGNAL-ONLY, no ranking effect).
    pr_chip = _peer_review_chip(cand.peer_review)
    if pr_chip:
        why.append(pr_chip)

    # 4. Version standing.
    if cand.version_type == "version_of_record":
        why.append("Peer-reviewed")
    elif cand.version_type == "preprint":
        why.append("Preprint")

    return why[:_MAX_WHY]


def attach_relevance(candidates: list[Candidate]) -> None:
    """Set each candidate's ``relevance_band`` + ``why`` IN PLACE (bands are
    pool-relative, so they're derived from the cohort itself)."""
    strong, mild = relevance_bands(candidates)
    for c in candidates:
        c.relevance_band = band_for(c.query_score, strong, mild)
        c.why = build_why(c, c.relevance_band)


__all__ = ["relevance_bands", "band_for", "build_why", "attach_relevance"]
