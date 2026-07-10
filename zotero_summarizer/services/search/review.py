"""Light-review tier + deep-set selector — the expert review's quality-timing fix.

The core defect the 2026-07-10 review caught (spec §13.7 Q2): if quality is only
measured AFTER relevance has chosen the deep-review set, an excellent paper ranked
4-8 can never be promoted — so rigor is not actually first-class. The fix is a
cheaper LIGHT review over the top ~8-10 (the query-INDEPENDENT quality checklist +
coverage), then a deep-set selector that picks 3-4 by relevance AND quality AND
diversity. Quality now changes WHAT gets deep-read.

Light review reuses ``quality_eval.evaluate_quality`` directly (read-only — no
Zotero write, no corpus membership). When no full text could be acquired, the
verdict is ``uncertain`` rather than a low grade (spec §9: essential sections
missing → uncertain), honoring the false-negative-averse persona.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.services.library.quality_eval import evaluate_quality
from zotero_summarizer.services.search._models import Candidate


def light_review(
    cand: Candidate,
    *,
    full_text: str,
    sections: list[dict[str, Any]],
    llm: Any,
    max_chars: int,
    self_consistency_runs: int = 1,
) -> None:
    """Run the query-independent quality checklist on one candidate (mutates
    ``cand.quality`` + ``cand.coverage``). Cheap tier: 1 self-consistency run by
    default. No full text → ``uncertain`` (spec §9)."""
    detected = [str(s.get("title") or "") for s in (sections or []) if s.get("title")]
    if not (full_text or "").strip():
        cand.quality = {"quality_band": "uncertain", "grade": "", "basis": "no_full_text"}
        cand.coverage = {"full_text_available": False, "sections_detected": detected}
        return
    result = evaluate_quality(
        title=cand.title,
        full_text=full_text,
        sections=sections or [],
        digest={},  # query-independent path; digest only feeds overstatement claims
        llm=llm,
        max_chars=max_chars,
        self_consistency_runs=self_consistency_runs,
    )
    cand.quality = result.model_dump()
    cand.coverage = {
        "full_text_available": True,
        "sections_detected": detected,
        "coverage_fraction": cand.quality.get("coverage_fraction"),
        "missing_critical": cand.quality.get("missing_critical") or [],
        "paper_type": cand.quality.get("paper_type") or "",
    }


def _uncertain(cand: Candidate) -> bool:
    return str(cand.quality.get("quality_band") or "").lower() == "uncertain"


def _quality_rank(cand: Candidate) -> float:
    band = str(cand.quality.get("quality_band") or "").lower()
    grade = str(cand.quality.get("grade") or "").upper()
    return {"highlight": 1.0, "flag": -1.0}.get(band, 0.0) + {
        "A": 0.3, "B": 0.15, "C": 0.0, "D": -0.15
    }.get(grade, 0.0)


def select_deep_set(candidates: list[Candidate], *, k: int = 4) -> list[Candidate]:
    """Pick the deep-review set from the light-reviewed, relevance-ranked list
    (spec §5 step 7b): the 2 highest-relevance, then the highest quality-adjusted
    among the rest, then optionally 1 uncertainty/diversity pick — so quality and
    exploration can pull in a paper relevance alone would not deep-read.

    ``candidates`` must already be ordered by the constrained rank contract."""
    if k <= 0 or not candidates:
        return []
    pool = candidates[: max(k * 2, 8)]  # the top ~8-10 light-reviewed band
    picked: list[Candidate] = list(pool[:2])  # 2 highest-relevance

    def take(cand: Candidate | None) -> None:
        if cand is not None and cand not in picked and len(picked) < k:
            picked.append(cand)

    rest = [c for c in pool if c not in picked]
    if rest:
        take(max(rest, key=_quality_rank))  # highest quality-adjusted
    rest = [c for c in pool if c not in picked]
    uncertain = [c for c in rest if _uncertain(c)]
    take(uncertain[0] if uncertain else (rest[0] if rest else None))  # exploration / next-best
    for cand in pool:  # top up to k in relevance order
        take(cand)
    return picked[:k]


__all__ = ["light_review", "select_deep_set"]
