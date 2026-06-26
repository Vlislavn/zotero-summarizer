"""Reference-free, author-blind full-text QUALITY evaluation.

Flags low-quality / highlights high-quality papers from the full text alone (no
citation counts), as a coarse 3-band verdict {flag/neutral/highlight} — never a
fine score (LLMs can't calibrate fine scales; ~3pt human-LLM error makes finer
granularity noise). Pipeline: cheap structural pre-filter + leakage red-flags →
decomposed yes/no/na rubric (3× self-consistency, bias-guarded) → RIGOURATE
overstatement check → conservative band aggregation with an asymmetric prestige
floor. ``quality_review.py`` (the digest LLM prompt) stays untouched; the rubric
logic lives here so each module keeps a single responsibility.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any

from zotero_summarizer.models import QualityEval
from zotero_summarizer.services.faithbench._corpus import PaperChunkIndex
from zotero_summarizer.services.library import _paper_type_checklists as pc
from zotero_summarizer.services.library import _quality_prompts as qp
from zotero_summarizer.services.library._grounding import quote_is_grounded
from zotero_summarizer.services.library._review_text import select_review_text

LOGGER = logging.getLogger(__name__)

# Structural signal patterns over the paper body (case-insensitive).
_PATTERNS: dict[str, re.Pattern[str]] = {
    "uncertainty": re.compile(r"\b(confidence interval|95%\s*ci|\bci\b|error bar|standard deviation|±|\bstd\b|p\s*[<=]|p-value|significan|multi-seed|across seeds)", re.I),
    "ablation": re.compile(r"\bablat", re.I),
    "external_validation": re.compile(r"\b(external validation|held[- ]out|independent (cohort|dataset|test)|out[- ]of[- ]distribution|external (cohort|test)|multi[- ]site|multi[- ]center)", re.I),
    "code_data_released": re.compile(r"(github\.com|gitlab\.com|zenodo\.org|huggingface\.co|osf\.io|figshare|/datasets?/|code (is )?(available|released)|publicly available)", re.I),
    "limitations": re.compile(r"\blimitation", re.I),
    "dataset_provenance": re.compile(r"\b(dataset|corpus|benchmark)\b.{0,80}\b(version|license|released|publicly|source|collected from|derived from)", re.I),
}
# Suspiciously perfect headline numbers without any leakage discussion. A bare near-1
# decimal (0.98 / 0.999) ALSO matches optimizer hyperparameters (Adam β2), dropout, and
# learning-rate schedules — so the value counts as a HEADLINE METRIC only when a
# performance-metric word sits next to it; without that gate, foundational methods papers
# (Transformer β2=0.98, BERT β2=0.999) were false-flagged on their optimizer settings.
_PERFECT_NUM = re.compile(r"\b(0\.9[89]\d*|1\.00|100(\.0)?%|99(\.\d+)?%)\b")
_METRIC_WORD = re.compile(
    r"\b(accuracy|accuracies|auroc|auprc|auc|f1|f-?score|dice|sensitivit|specificit|"
    r"precision|recall|iou|bleu|rouge|meteor|exact[- ]match|top-?\d|error[- ]rate|score|"
    r"achiev|reach|obtain|attain|outperform|state[- ]of[- ]the[- ]art|sota|correlation|"
    r"performance)\b", re.I)
_LEAKAGE_WORD = re.compile(r"\b(leakage|data leak|contaminat|patient[- ]level split|group(ed)?[- ]split)", re.I)


def _near_perfect_metric(text: str) -> bool:
    """A value near 1.0 / 100% reported AS a performance metric — i.e. a metric word
    within ~40 chars of it. Excludes bare near-1 decimals that are really hyperparameters
    (Adam β2, dropout, learning-rate schedules), the false positive that capped
    Transformer/BERT to ``flag`` despite A-grade coverage. ponytail: metric-word
    allowlist; broaden it if a genuine near-perfect metric is ever missed."""
    return any(_METRIC_WORD.search(text[max(0, m.start() - 40): m.end() + 40])
               for m in _PERFECT_NUM.finditer(text))
_CLINICAL = re.compile(r"\b(patient|clinical|diagnos|EHR|medical|cohort|disease|hospital|genom|bioinform|cell|protein)", re.I)
_AGENTIC = re.compile(r"\b(agent|autonom|multi[- ]agent|tool[- ]use|policy enforcement|orchestrat|llm[- ]agent)", re.I)


def _structural(sections: list[dict[str, Any]], full_text: str) -> tuple[dict[str, bool], list[str]]:
    """Cheap high-precision presence checks + leakage red-flags over the body."""
    section_titles = " ".join(str(s.get("title") or "") for s in (sections or []))
    haystack = f"{section_titles}\n{full_text}"
    signals = {name: bool(rx.search(haystack)) for name, rx in _PATTERNS.items()}
    red_flags: list[str] = []
    # Leakage: a near-perfect headline METRIC (not an optimizer hyperparameter) with no
    # leakage discussion anywhere.
    if _near_perfect_metric(full_text) and not _LEAKAGE_WORD.search(full_text):
        red_flags.append("near-perfect headline metric with no leakage/contamination discussion")
    # Clinical paper using plain cross-validation without a patient-level split mention.
    # Match patient/subject/group-level terminology ANYWHERE (papers write
    # "patient-level 5-fold cross-validation" — digits/words sit between the terms).
    if _CLINICAL.search(full_text) and re.search(r"\b(k[- ]?fold|cross[- ]validation)\b", full_text, re.I) \
            and not re.search(r"\b(patient|subject|group)[- ]level\b|grouped (k[- ]?fold|cv)", full_text, re.I):
        red_flags.append("clinical data with cross-validation but no patient-level split stated")
    return signals, red_flags


def _run_rubric(
    llm: Any, *, title: str, body: str, structural: dict[str, bool], items_block: str,
    exemplars: str, runs: int, reporter: Any = None, sub_concurrency: int = 1,
) -> list[qp.RubricLLMResponse]:
    """Self-consistency samples of the decomposed rubric (runs ≥ 1).

    When ``sub_concurrency > 1`` (remote provider) all samples are dispatched
    concurrently via a thread pool — the prompt is identical for every run so
    the aggregation/majority-vote result is unaffected. Sub-progress is reported
    via the thread-safe ``reporter.sub()`` as each future completes.
    When ``sub_concurrency == 1`` (local provider) the serial path is kept to
    protect host RAM.
    """
    prompt = qp.QUALITY_RUBRIC_PROMPT.format(
        title=title or "(hidden)", full_text=body,
        structural=", ".join(k for k, v in structural.items() if v) or "none detected",
        items=items_block, exemplars=(exemplars + "\n\n") if exemplars else "",
    )
    total = max(1, runs)

    if sub_concurrency <= 1 or total <= 1:
        samples: list[qp.RubricLLMResponse] = []
        for i in range(total):
            samples.append(llm.pydantic_prompt(prompt=prompt, pydantic_model=qp.RubricLLMResponse))
            if reporter is not None:
                reporter.sub(i + 1, total)
        return samples

    # Parallel path: fan out all `total` calls, report monotonically as each lands.
    done_counter = 0
    counter_lock = Lock()
    ordered: list[qp.RubricLLMResponse | None] = [None] * total

    def _one(idx: int) -> tuple[int, qp.RubricLLMResponse]:
        return idx, llm.pydantic_prompt(prompt=prompt, pydantic_model=qp.RubricLLMResponse)

    with ThreadPoolExecutor(max_workers=min(sub_concurrency, total)) as pool:
        futures = {pool.submit(_one, i): i for i in range(total)}
        for future in as_completed(futures):
            idx, result = future.result()  # raises on LLM error → propagates
            ordered[idx] = result
            nonlocal_done = 0
            with counter_lock:
                done_counter += 1
                nonlocal_done = done_counter
            if reporter is not None:
                reporter.sub(nonlocal_done, total)

    return [s for s in ordered if s is not None]


def _majority(values: list[str], default: str = "na") -> str:
    vals = [v for v in values if v]
    return Counter(vals).most_common(1)[0][0] if vals else default


def _dedupe_near(items: list[str], *, jaccard: float = 0.6) -> list[str]:
    """Merge NEAR-duplicate red-flag strings. The 3 self-consistency runs phrase the
    same concern slightly differently ("does not address run-to-run determinism" vs
    "does not report run-to-run determinism"), so an exact ``set()`` left both. Keep
    the first; drop a later item whose content-token set overlaps an earlier kept item
    by >= ``jaccard`` (token-Jaccard, deterministic, no model)."""
    kept: list[str] = []
    kept_tokens: list[set[str]] = []
    for it in items:
        toks = {w for w in re.findall(r"[a-z0-9]+", it.lower()) if len(w) > 2}
        dup = any(kt and len(toks & kt) / len(toks | kt) >= jaccard for kt in kept_tokens)
        if not dup:
            kept.append(it)
            kept_tokens.append(toks)
    return kept


def _aggregate_rubric(samples: list[qp.RubricLLMResponse], item_keys: list[str], body: str
                      ) -> tuple[dict[str, str], dict[str, str], list[str], set[str]]:
    """Per-item majority verdict + a grounded evidence quote; returns
    ``(rubric, evidence, concerns, grounded_yes_keys)``."""
    rubric: dict[str, str] = {}
    evidence: dict[str, str] = {}
    grounded_yes: set[str] = set()
    for key in item_keys:
        verdicts = [str(s.checks.get(key, "")).strip().lower() for s in samples]
        verdict = _majority([v for v in verdicts if v in {"yes", "no", "na"}])
        rubric[key] = verdict
        # Prefer a GROUNDED quote from ANY sample that voted this verdict (don't
        # stop at the first sample — its quote may be ungrounded while a later
        # sample's is grounded, which is what earns the HIGHLIGHT coverage).
        quotes = [q for s in samples
                  if str(s.checks.get(key, "")).strip().lower() == verdict
                  for q in [str(s.evidence.get(key) or "").strip()] if q]
        grounded = next((q for q in quotes if quote_is_grounded(q, body, fuzzy=True)), None)
        if grounded is not None:
            evidence[key] = grounded
            if verdict == "yes":
                grounded_yes.add(key)
        elif quotes:
            evidence[key] = quotes[0]
    concerns = sorted({c.strip() for s in samples for c in (s.concerns or []) if c and c.strip()})[:5]
    return rubric, evidence, concerns, grounded_yes


def _overstatements(llm: Any, *, claims: list[str], index: PaperChunkIndex) -> list[str]:
    """RIGOURATE: abstract headline claims the body does not support."""
    claims = [c for c in claims if c and c.strip()][:4]
    if not claims:
        return []
    passages = "\n".join(
        f"[claim: {c[:80]}]\n" + "\n".join(index.top_chunks(c, 3)) for c in claims
    )
    prompt = qp.OVERSTATEMENT_PROMPT.format(claims="\n".join(f"- {c}" for c in claims), passages=passages)
    parsed = llm.pydantic_prompt(prompt=prompt, pydantic_model=qp.OverstatementLLMResponse)
    return [o.strip() for o in (parsed.overstatements or []) if o and o.strip()][:5]


def _shadow_claim_scores(claims: list[str], index: PaperChunkIndex, model_name: str) -> dict[str, float]:
    """Phase A SHADOW: score each headline claim with the MiniCheck ENCODER over
    the SAME retrieved evidence the LLM judge saw, for a reproducible A/B. Recorded
    and logged; does NOT change the band or overstatements. Empty dict when the
    encoder is unavailable (the LLM verdict stands)."""
    cleaned = [c for c in claims if c and c.strip()][:4]
    if not cleaned:
        return {}
    from zotero_summarizer.services.model.claim_checker import get_claim_checker
    evidences = ["\n".join(index.top_chunks(c, 3)) for c in cleaned]
    probs = get_claim_checker(model_name).score(cleaned, evidences)
    if probs is None:
        return {}
    LOGGER.info("shadow claim-check (%s) support probs: %s",
                model_name, ", ".join(f"{p:.2f}" for p in probs))
    return {c: round(p, 4) for c, p in zip(cleaned, probs)}


def _self_verify(llm: Any, *, spec: pc.ChecklistSpec, rubric: dict[str, str],
                 evidence: dict[str, str], grounded_yes: set[str]) -> set[str]:
    """Second pass (AI-Scientist-style self-reflection): re-check the CRITICAL items a
    first pass marked MET — does the grounding quote ACTUALLY establish the criterion,
    or did the rubric LLM over-claim (positivity bias)? Returns the set of critical
    keys to DEMOTE. ONE LLM call over (criterion, quote) pairs; empty when there is
    nothing critical+met to verify. A key the verifier omits is NOT demoted (we only
    overturn an explicit ``reject`` — conservative)."""
    critical_met = [it for it in spec.items
                    if it.critical and rubric.get(it.key) == "yes"
                    and it.key in grounded_yes and (evidence.get(it.key) or "").strip()]
    if not critical_met:
        return set()
    items_block = "\n".join(
        f'- {it.key}: CRITERION="{it.question}" QUOTE="{evidence[it.key].strip()}"'
        for it in critical_met
    )
    resp = llm.pydantic_prompt(
        prompt=qp.SELF_VERIFY_PROMPT.format(items=items_block),
        pydantic_model=qp.SelfVerifyResponse,
    )
    return {it.key for it in critical_met
            if str(resp.verdicts.get(it.key, "confirm")).strip().lower() == "reject"}


def evaluate_quality(
    *, title: str, full_text: str, sections: list[dict[str, Any]], digest: dict[str, Any],
    llm: Any, max_chars: int, paper_type: str | None = None,
    prestige_score: float | None = None, prestige_known: bool = False,
    prestige_floor: float | None = None, self_consistency_runs: int = 3, exemplars: str = "",
    shadow_claim_check: bool = False, claim_check_model: str = "flan-t5-large",
    self_verification: bool = False, reporter: Any = None, sub_concurrency: int = 1,
) -> QualityEval:
    """Compute the reference-free quality verdict, judging the paper against the
    recognized reporting/appraisal standard for its ``paper_type`` (a review is NOT
    judged on ablations/leakage). The band is derived from CHECKLIST COVERAGE, not an
    LLM number. ``prestige_*`` are accepted for call-site compatibility but no longer
    affect the quality band — prestige is a ranking signal, not a reporting-quality
    one. Errors propagate — the caller wraps this layer."""
    # Resolve the type (unknown / None → safe GENERIC_EMPIRICAL supertype) and its
    # checklist; ``resolved`` is what we record so the UI shows the standard applied.
    try:
        resolved = pc.PaperType(paper_type or "").value
    except ValueError:
        resolved = pc.PaperType.GENERIC_EMPIRICAL.value
    spec = pc.spec_for(resolved)

    # Budget-aware selection (referee-critical sections + ranked chunks); the
    # structural checks below still see the FULL text — only the LLM-fed body is curated.
    body = select_review_text(sections, full_text or "", budget=max_chars)
    signals, structural_flags = _structural(sections, full_text or "")
    # The structural leakage red-flags are EMPIRICAL — never fire them on a
    # review/policy/position paper (the exact bug being fixed).
    red_flags = structural_flags if pc.Family.EMP in spec.families else []
    item_keys = [it.key for it in spec.items]
    items_block = "\n".join(f"- {it.key}: {it.question}" for it in spec.items)
    if reporter is not None:
        reporter.phase("quality_rubric", total=max(1, self_consistency_runs))
    samples = _run_rubric(llm, title=title, body=body, structural=signals, items_block=items_block,
                          exemplars=exemplars, runs=self_consistency_runs, reporter=reporter,
                          sub_concurrency=sub_concurrency)
    rubric, evidence, concerns, grounded_yes = _aggregate_rubric(samples, item_keys, body)

    # Self-verification (second pass): overturn over-claimed CRITICAL "met" items so a
    # rejected one becomes a missing critical (demotes the band). Runs BEFORE the
    # per-sample/coverage banding so the correction propagates to both.
    demoted: set[str] = set()
    if self_verification:
        if reporter is not None:
            reporter.phase("quality_verify", is_call=True)
        demoted = _self_verify(llm, spec=spec, rubric=rubric, evidence=evidence, grounded_yes=grounded_yes)
        for k in demoted:
            rubric[k] = "no"
            grounded_yes.discard(k)

    claims = [str(digest.get("tldr") or "")] + [str(x) for x in (digest.get("key_findings") or [])]
    chunk_index = PaperChunkIndex(body)
    if reporter is not None:
        reporter.phase("quality_overstate", is_call=True)
    overstatements = _overstatements(llm, claims=claims, index=chunk_index)
    claim_support = _shadow_claim_scores(claims, chunk_index, claim_check_model) if shadow_claim_check else {}

    # Structural leakage red_flags (EMP-only) are HARD. Abstract-vs-body overstatements are
    # a SOFT signal: 1-2 of them (often false-positives on a faithful paper) must not alone
    # drag a well-covered paper to `flag` — require a cluster (>=3). ponytail: count gate;
    # upgrade to coverage-aware gating (overstatements cap only when coverage is weak) if needed.
    has_red_flag = bool(red_flags) or len(overstatements) >= 3
    # Per-sample band agreement → "uncertain" reflects VERDICT variance across runs
    # (grounding is resolved at the aggregate level via the shared `grounded_yes`).
    per_sample = [
        pc.coverage_grade(
            spec, {k: str(s.checks.get(k, "")).strip().lower() for k in item_keys},
            grounded_yes, has_red_flag=has_red_flag,
        ).band
        for s in samples
    ]
    coverage = pc.coverage_grade(spec, rubric, grounded_yes, has_red_flag=has_red_flag)
    agreed = len(set(per_sample)) == 1
    band = coverage.band if agreed else "uncertain"
    agreement = per_sample.count(coverage.band) / len(per_sample) if per_sample else 0.0
    confidence = round(agreement * (0.7 if overstatements else 1.0), 2)
    domain = "clinical_bio" if _CLINICAL.search(full_text or "") else ("agentic" if _AGENTIC.search(full_text or "") else "general")

    return QualityEval(
        quality_band=band, grade=coverage.grade, rubric=rubric, evidence=evidence,
        red_flags=_dedupe_near(red_flags + concerns), overstatements=overstatements,
        claim_grounding_rate=0.0,  # filled by the faithbench aspect track (Phase 5)
        claim_support_probs=claim_support,  # shadow A/B only; does not affect the band
        confidence=confidence, passes_agreed=per_sample.count(coverage.band), passes_total=len(per_sample),
        domain=domain, paper_type=resolved,
        coverage_standard=" / ".join(name for name, _ in spec.standards),
        coverage_met=coverage.met, coverage_applicable=coverage.applicable,
        coverage_fraction=coverage.fraction, missing_critical=coverage.missing_critical,
        self_verification_demoted=sorted(demoted),
    )
