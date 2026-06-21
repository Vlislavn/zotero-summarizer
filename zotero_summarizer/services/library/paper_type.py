"""Detect a paper's TYPE so the quality engine judges it against the right standard.

Layered + lazy, no trained model: a cheap metadata/structural PRIOR narrows the
space, then ONE LLM classification call decides. Low confidence (or a contradiction
with a hard metadata signal) falls back to a safe SUPERTYPE — so a review is never
judged as an empirical paper, and vice-versa, even when the classifier is unsure.

Detection is cheap and gate-independent; the caller (``_deep_review_layers.extra_layers``)
treats it as one more independently-skippable layer and degrades to ``GENERIC_EMPIRICAL``
on failure (the module's established background-worker boundary). Within ``detect`` the
LLM error propagates — we don't silently swallow it here.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from zotero_summarizer.services.library._paper_type_checklists import CHECKLISTS, Family, PaperType

# Below this LLM confidence we don't trust the leaf type → safe supertype.
_CONF_FLOOR = 0.55

# Leaf types the classifier may pick (the GENERIC_* supertypes are fallback-only).
_LEAF_TYPES = tuple(t.value for t in PaperType if not t.value.startswith("generic_"))

# Structural signals over section headings + body (hints for the LLM + the fallback).
_SIGNALS: dict[str, re.Pattern[str]] = {
    # Empirical-section signal: Methods/Results/Experiments only — NOT Intro/Discussion
    # (every paper type has those, so they don't distinguish empirical from review).
    "imrad": re.compile(r"\b(method(s|ology)?|results|experiments?|evaluation setup)\b", re.I),
    "propose": re.compile(r"\b(we propose|we introduce|we present|we develop|our (method|approach|model|framework|system))\b", re.I),
    "prisma": re.compile(r"\b(PRISMA|databases searched|inclusion criteria|search strategy|records identified|data extraction|meta-?analys)\b", re.I),
    "rct": re.compile(r"\b(randomi[sz]ed|allocation concealment|intention[- ]to[- ]treat|double[- ]blind|primary endpoint|clinicaltrials\.gov|NCT\d{8})\b", re.I),
    "diagnostic": re.compile(r"\b(sensitivity and specificity|\bROC\b|AUROC|reference standard|diagnostic accuracy|radiolog|patholog|segmentation)\b", re.I),
    "prediction": re.compile(r"\b(prediction model|prognostic|risk (score|model)|calibration|c[- ]statistic|nomogram|discriminat)\b", re.I),
    "dataset": re.compile(r"\b(we (introduce|present|release) (a )?(new )?(dataset|benchmark|corpus)|leaderboard|benchmark suite)\b", re.I),
    "review": re.compile(r"\b(in this (review|survey)|we (review|survey)|this (review|survey)|comprehensive (review|survey)|literature review|taxonomy of)\b", re.I),
    "position": re.compile(r"\b(we argue|we contend|in this perspective|position paper|we advocate|call to action)\b", re.I),
    "policy": re.compile(r"\b(we recommend|recommendations|consensus|guideline|best practice|expert panel|Delphi|working group|should be reported)\b", re.I),
    "theory": re.compile(r"\b(theorem|lemma|\bproof\b|proposition|corollary|we prove)\b", re.I),
    "case_report": re.compile(r"\b(case report|case series|[0-9]+[- ]year[- ]old (man|woman|male|female|patient)|we present a case|patient presented)\b", re.I),
}

# Coarse Zotero itemType → hint passed to the classifier (itemType can't separate
# review vs empirical within journalArticle, so it's only a weak prior).
_ITEMTYPE_HINT: dict[str, str] = {
    "case": "case_report", "report": "policy/guideline", "thesis": "empirical",
    "conferencePaper": "empirical/methods", "journalArticle": "(ambiguous)",
    "preprint": "(ambiguous)", "book": "review/survey", "bookSection": "review/position",
}

_REVIEW_FAMILY = {Family.SYNTH, Family.ARG}


class _TypeVerdict(BaseModel):
    """One LLM classification of the paper's type."""

    paper_type: str = ""
    confidence: float = Field(default=0.5)
    reasoning: str = ""
    secondary: str = ""

    @field_validator("paper_type", "secondary")
    @classmethod
    def _normalise(cls, v: str) -> str:
        v = (v or "").strip().lower()
        return v if v in _LEAF_TYPES else ""

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


_TYPE_DEFS = "\n".join(
    f"- {t.value}: {CHECKLISTS[t].label}" for t in PaperType if not t.value.startswith("generic_")
)

_PROMPT = (
    "You classify a scholarly paper by its PRIMARY contribution / study design — NOT by "
    "topic. Pick exactly one type from this list:\n{type_defs}\n\n"
    "Rules: a paper with original experiments + Methods/Results is empirical_ml (or "
    "clinical_prediction if it develops/validates a model predicting a patient outcome; "
    "diagnostic_accuracy if it reports sensitivity/specificity vs a reference standard; "
    "rct_ai if randomized/prospective on patients). A paper that synthesizes the "
    "literature with a search protocol is systematic_review; without a protocol it is "
    "narrative_review or survey. A paper that argues a thesis with little/no new data is "
    "position; one that issues recommendations/consensus is policy. Theorems/proofs → "
    "theory. Classify by what the paper DID, even if the metadata hint is generic.\n\n"
    "Zotero item-type hint: {item_hint}\n"
    "Structural signals detected: {signals}\n\n"
    "Title: {title}\nAbstract: {abstract}\nSection headings: {headings}\n\n"
    "Return ONE strict JSON object: "
    '{{"paper_type": "<one type>", "confidence": <0-1>, "reasoning": "<one sentence>", '
    '"secondary": "<runner-up type or empty>"}}. Start {{ end }}.'
)


def _structural_signals(headings: list[str] | None, full_text: str) -> dict[str, bool]:
    """Cheap presence checks over section headings + body (hints + fallback input)."""
    haystack = " ".join(str(h or "") for h in (headings or [])) + "\n" + (full_text or "")
    return {name: bool(rx.search(haystack)) for name, rx in _SIGNALS.items()}


def _safe_supertype(signals: dict[str, bool]) -> PaperType:
    """When the classifier is unsure, pick the SAFE supertype from structural signals:
    review-shaped (synthesis/argument, no experiments) → GENERIC_REVIEW, else
    GENERIC_EMPIRICAL. Erring toward empirical is fine — its checklist's empirical
    items just come back N/A for a non-empirical paper; the danger we avoid is judging
    a clearly review-shaped paper on empirical criteria."""
    review_shaped = any(signals.get(k) for k in ("prisma", "review", "position", "policy"))
    # "Has experiments" keys on the STRONG we-built/ran-it signals only. A generic
    # "methodology" section (`imrad`) is NOT enough — a consensus guideline legitimately
    # describes its Delphi *methodology*, and counting that as empirical mis-routed the
    # real ESMO EBAI paper to generic_empirical (surfaced by the real-paper eval).
    has_experiments = signals.get("propose") or signals.get("rct")
    if review_shaped and not has_experiments:
        return PaperType.GENERIC_REVIEW
    return PaperType.GENERIC_EMPIRICAL


def _contradicts_metadata(ptype: str, item_type: str | None) -> bool:
    """A hard metadata fact the LLM should not overrule (e.g. itemType=case → the
    paper IS a case report; an LLM 'empirical_ml' guess contradicts it)."""
    if (item_type or "").strip() == "case":
        return ptype != PaperType.CASE_REPORT.value
    return False


def detect(
    *, title: str, abstract: str, headings: list[str] | None, full_text: str,
    item_type: str | None = None, llm: Any, override: str | None = None,
) -> dict[str, Any]:
    """Return ``{type, confidence, source, reasoning, secondary, uncertain, signals}``.

    ``override`` (a per-item user correction) wins outright. Otherwise: structural
    prior → one LLM call → safe-supertype fallback when low-confidence or contradicting
    a hard metadata fact."""
    signals = _structural_signals(headings, full_text)
    if override:
        ov = override.strip().lower()
        if ov in _LEAF_TYPES:
            return {"type": ov, "confidence": 1.0, "source": "override",
                    "reasoning": "user override", "secondary": "", "uncertain": False,
                    "signals": signals}

    active = ", ".join(k for k, v in signals.items() if v) or "none"
    verdict = llm.pydantic_prompt(
        prompt=_PROMPT.format(
            type_defs=_TYPE_DEFS, item_hint=_ITEMTYPE_HINT.get((item_type or "").strip(), item_type or "(none)"),
            signals=active, title=title or "(untitled)", abstract=(abstract or "(none)")[:1500],
            headings=", ".join(str(h) for h in (headings or [])[:25]) or "(none)",
        ),
        pydantic_model=_TypeVerdict,
    )

    if not verdict.paper_type or verdict.confidence < _CONF_FLOOR \
            or _contradicts_metadata(verdict.paper_type, item_type):
        return {"type": _safe_supertype(signals).value, "confidence": verdict.confidence,
                "source": "fallback", "reasoning": verdict.reasoning or "low confidence",
                "secondary": verdict.paper_type, "uncertain": True, "signals": signals}

    return {"type": verdict.paper_type, "confidence": verdict.confidence, "source": "llm",
            "reasoning": verdict.reasoning, "secondary": verdict.secondary,
            "uncertain": False, "signals": signals}


def detect_safe(ctx: Any, sections: list[dict[str, Any]], body: str, logger: Any) -> dict[str, Any]:
    """``detect`` with the deep-review boundary fallback (an independently-skippable
    layer): build the inputs from the review ctx, and on ANY failure log + return the
    safe GENERIC_EMPIRICAL supertype instead of blocking the review. ``ctx`` is
    duck-typed (``.title``/``.digest_dump``/``.llm``, optional ``.item_type``/
    ``.paper_type_override``) to avoid importing deep_review's dataclass."""
    try:
        headings = [str(s.get("title") or "") for s in (sections or [])]
        abstract = " ".join(str((ctx.digest_dump or {}).get(k) or "")
                            for k in ("tldr", "executive_summary")).strip()
        return detect(title=ctx.title, abstract=abstract, headings=headings, full_text=body,
                      item_type=getattr(ctx, "item_type", None), llm=ctx.llm,
                      override=getattr(ctx, "paper_type_override", None))
    except Exception as exc:  # noqa: BLE001 — independently-skippable layer (design)
        logger.warning("paper-type detection failed: %s", exc)
        return {"type": PaperType.GENERIC_EMPIRICAL.value, "confidence": 0.0,
                "source": "error", "uncertain": True, "reasoning": str(exc)}


__all__ = ["detect", "detect_safe"]
