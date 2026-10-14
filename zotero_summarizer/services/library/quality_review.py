"""Condensed paper-digest assessment for the deep-review path.

Fetches nothing itself — the caller (``services.library.deep_review``) supplies the
already-extracted full text and asks the reasoning LLM for a condensed, scannable
digest (the user's 7-point investigation + a referee-grade quality call), judged on
the paper's own merits and personalised to the user's research goals.

Genuine errors (LLM failure, malformed JSON) propagate to the caller — the feeds
background loop records them per-row rather than inventing a fake review.
"""
from __future__ import annotations

from typing import Any

from zotero_summarizer.models import GoalsConfig, PaperDigest
from zotero_summarizer.integrations.llm import build_response_format
from zotero_summarizer.services._common import extract_json_blob, to_text
from zotero_summarizer.services.library._digest_verification import DigestVerifierUnavailable
from zotero_summarizer.services.library import _digest_audit
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input
from zotero_summarizer.services.library._review_text import select_review_text

# Reinforced-retry suffix: a reasoning model (e.g. a remote endpoint) occasionally
# emits a digest with out-of-range scores (0) or malformed JSON. Re-ask once, strictly,
# before propagating the error to the caller's per-item boundary.
_STRICT_RETRY_SUFFIX = (
    "\n\nIMPORTANT: your previous reply was not valid. Return ONE JSON object only "
    "(no prose, no code fences). Every score field (soundness/novelty/significance/"
    "reproducibility/clarity) MUST be an integer from 1 to 5 — never 0. Include "
    "all reading-action and writing fields explicitly. Reading action MUST be read, skim or skip. "
    "Read/skim need a concrete "
    "reason, exact target, positive minute estimate, and original-only value."
)

_INTERPRETATION_SUFFIX = (
    "\n\nExperimental AI-draft interpretation: additionally return `interpretation` with "
    "`items`, or null when unavailable. Each item references an existing finding using "
    "`finding_ref` = /tldr or /key_findings/<zero-based index>; NEVER copy finding text. "
    "Include `inference_strength`, `rationale`, `scope` (strings), `uncertainties` and "
    "`unsupported_extrapolations` (lists of strings), and `basis_refs` (existing /methods, "
    "/limitations, /key_weakness, /parameters or /parameters/<field> pointers). "
    "Use source-specific evidence and distinguish missing information from contradiction. "
    "Do not infer causal warrant from observational design alone; do not confuse source "
    "availability with scientific scope. Leave unknown scientific text empty rather than "
    "inventing it. Do not supply origin, approval or verification state: this is UNVERIFIED "
    "AI-draft content, not author-approved or scientifically certified."
)


class DigestGenerationFailed(ValueError):
    """A returned digest cannot satisfy the publication schema."""


def _coerce_digest(raw: Any, *, interpretation_out: dict[str, Any] | None = None) -> PaperDigest:
    """Salvage digest JSON; C content is captured before projection to a plain base."""
    if interpretation_out is not None:
        interpretation_out.clear()
    try:
        if interpretation_out is not None:
            from zotero_summarizer.models.interpretation import InterpretationDigestResponse, interpretation_payload

            payload = raw.model_dump(exclude_unset=True) if isinstance(raw, PaperDigest) else extract_json_blob(to_text(raw))
            raw = InterpretationDigestResponse.model_validate(payload)
            content = raw.interpretation
        if type(raw) is PaperDigest:
            digest = raw
        elif isinstance(raw, PaperDigest):
            present = raw.model_fields_set.intersection(PaperDigest.model_fields)
            digest = PaperDigest.model_validate(raw.model_dump(include=present))
            digest.model_fields_set.intersection_update(present)
        else:
            digest = PaperDigest.model_validate(extract_json_blob(to_text(raw)))
    except ValueError as exc:
        raise DigestGenerationFailed(str(exc)) from exc
    required = {
        "read_decision", "read_why", "read_parts", "skip_parts",
        "estimated_read_minutes", "original_value", "writing_friction", "writing_reasons",
    }
    if not required <= digest.model_fields_set:
        raise DigestGenerationFailed("digest omitted required reading or writing assessment")
    if digest.read_decision not in {"read", "skim", "skip"}:
        raise DigestGenerationFailed("digest reading action must be read, skim or skip")
    if digest.read_decision in {"read", "skim"}:
        if not digest.read_why.strip() or not any(part.strip() for part in digest.read_parts):
            raise DigestGenerationFailed("read/skim requires a reason and exact target")
        if not digest.original_value.strip() or not (digest.estimated_read_minutes or 0) > 0:
            raise DigestGenerationFailed("read/skim requires original-only value and positive minutes")
    if interpretation_out is not None:
        interpretation_out.update(interpretation_payload(content, digest))
    return digest


def _digest_response_contract(
    prompt: str, response_format: dict[str, Any] | None, interpretation_out: dict[str, Any] | None,
) -> tuple[type[PaperDigest], str, dict[str, Any]]:
    extra = {"response_format": response_format} if response_format else {}
    if interpretation_out is None:
        return PaperDigest, prompt, extra
    from zotero_summarizer.models.interpretation import InterpretationDigestResponse

    if response_format:
        extra = {"response_format": build_response_format(InterpretationDigestResponse)}
    return InterpretationDigestResponse, prompt + _INTERPRETATION_SUFFIX, extra


def _generate_digest(
    llm: Any, prompt: str, response_model: type[PaperDigest], *, stage: str,
    extra: dict[str, Any], interpretation_out: dict[str, Any] | None,
) -> PaperDigest:
    from zotero_summarizer.services.library._review_attempt import observed_client

    if interpretation_out is not None:
        interpretation_out.clear()
    raw = observed_client(llm, stage).pydantic_prompt(prompt=prompt, pydantic_model=response_model, **extra)
    return _coerce_digest(raw, interpretation_out=interpretation_out)


def _verify_generated_digest(
    digest: PaperDigest, text: str, verifier: Any, generator: Any, goals: str, *, attempt: dict | None = None,
) -> Any:
    from zotero_summarizer.services.library._digest_verification import verify_digest
    from zotero_summarizer.services.library._review_attempt import record

    try:
        return verify_digest(digest, text, verifier, research_goals=goals)
    except DigestVerifierUnavailable:
        if attempt is not None:
            attempt.setdefault("unavailable_routes", []).append({"route": "primary", "reason": "malformed_verifier_output"})
        if verifier is generator:
            raise
        record('verifier_fallback', {'availability': 'generator_fallback'}, kind='identity')
        if attempt is not None:
            attempt["verifier_route"] = "generator_fallback"
        return verify_digest(digest, text, generator, research_goals=goals)

# Fallback when goals.yaml has no `prompts.paper_digest`. A referee-grade digest
# (NeurIPS/ICLR rubric) condensed into scannable fields, personalised to the
# reader's goals. Brevity is enforced hard — a note, not a wall of text — and
# every judgement must be grounded in the paper's own evidence (no confabulation).
_DEFAULT_DIGEST_PROMPT = (
    "You are a meticulous peer reviewer (NeurIPS/ICLR calibre). Read the paper's full "
    "text and produce a CONDENSED, scannable referee digest the reader will skim months "
    "from now.\n\n"
    "### ANTI-FABRICATION — the single most important rule\n"
    "Write ONLY numbers, percentages, metrics, dataset names, URLs and named entities that "
    "appear VERBATIM in the paper text below. Before you write ANY number or percentage, "
    "confirm it is literally present in the text; if it is not there, DO NOT write it. "
    "If the paper reports no quantitative results (e.g. a perspective/position paper), then "
    "`key_findings` MUST be qualitative or empty — NEVER invent statistics like '45%' or "
    "'2x' to fill it. A digest that leaves a field empty is GOOD; a digest with even one "
    "invented number, link, or result is a FAILURE worse than an empty one. When unsure "
    "whether a detail is in the paper, omit it.\n\n"
    "Ground EVERY judgement in the paper's own evidence; if the text does not support a "
    "field, leave it empty (\"\") or neutral. Be terse: each text field is ONE short sentence "
    "(<=25 words); lists hold at most 3 brief bullets. No fluff, do not restate the title.\n\n"
    "The reader's research goals: {research_goals}\n\n"
    "Title: {title}\n\nFull text (may be truncated):\n{full_text}\n\n"
    "Review like a referee: pin down the main CONTRIBUTION, weigh the EVIDENCE behind "
    "the central claim, compare NOVELTY against prior work, and judge whether the "
    "experiments actually support the conclusions. Then return a single strict JSON "
    "object with these fields:\n"
    '- "tldr": the paper\'s main contribution in one sentence.\n'
    '- "verdict": a one-line referee summary judgement (what it does + how convincingly).\n'
    "Start at `skip`. Use `read` ONLY if all hold: direct active-goal support; the "
    "original may change a current decision; compression loses important nuance; evidence "
    "is strong; avoidable writing friction is not high; and the contribution is not redundant. Otherwise skim/skip.\n"
    '- "read_decision": exactly one of "read", "skim", "skip".\n'
    '- "read_why": a concrete reason; for skip, say the original adds little value now.\n'
    '- "read_parts": 1-4 exact section/figure/table/appendix/code locators for skim/read; [] for skip.\n'
    '- "skip_parts": low-value parts safe to ignore ([] if none).\n'
    '- "estimated_read_minutes": total minutes for the recommended action (null if unknown).\n'
    '- "original_value": what opening the original adds beyond this digest ("" for skip).\n'
    "Audit writing independently BEFORE choosing the reading action. Actively seek recurring counterexamples to: "
    "central terms before use; one stated axis per taxonomy; signposted abstraction shifts; explicit claim/evidence "
    "links. Ignore isolated typos, technical depth, study validity, weak evidence, and sophistication. MUST return both "
    "writing fields: zero counterexamples = low, one = moderate, two or more or a reconstructed central argument = high.\n"
    '- "writing_friction": exactly "low", "moderate", or "high" from the counterexample count.\n'
    '- "writing_reasons": each concrete counterexample (1-3 for moderate/high; [] for low).\n'
    '- "relevance": how it connects to the reader\'s research goals above (one sentence; "" if none).\n'
    '- "controversies": the most debatable claim and why it is contestable (one sentence; "" if none).\n'
    '- "impact": likely effect on the field if the claims hold (one sentence).\n'
    '- "unknown_unknowns": a useful implication the reader likely did not consider (one sentence).\n'
    '- "implementation": concrete reusable steps/artifacts only (<=3 bullets; [] if unsupported).\n'
    '- "executive_summary": 3-5 sentence neutral overview — what the paper is, its contribution, the headline result.\n'
    '- "key_findings": up to 5 concrete findings, each with its metric/number when stated (list of short strings).\n'
    '- "methods": dataset provenance, preprocessing, architecture and training setup, in one sentence.\n'
    '- "limitations": the stated limitations and the most material gaps, in one sentence.\n'
    '- "industry_impact": likely effect on industry/practice in one short line ("" if none).\n'
    '- "academy_impact": likely effect on academic research in one short line ("" if none).\n'
    '- "parameters": a JSON object of technical parameters extracted VERBATIM from the full text — '
    '"dataset" (name(s) + scale, one clause), "baselines" (<=3 named comparison methods), '
    '"sample_size" (the reported N/cohort/epochs as a short string), "metrics" (<=3 named metrics '
    'with their reported values when stated), "architecture" (model/system architecture in one '
    'clause), "external_validation" (true/false/omit — was it validated on an external/out-of-sample '
    'set?). OMIT any field the paper does not state; an empty/omitted field is correct, an invented '
    'one is a failure. Set "parameters" to null for non-empirical papers (perspectives, surveys).\n'
    '- "key_strength": the single strongest aspect, grounded in the text (one clause).\n'
    '- "key_weakness": the single most material limitation or threat to validity (one clause).\n'
    '- "grade": overall quality A (excellent)/B (solid)/C (borderline)/D (weak).\n'
    '- "soundness": integer 1-5 — are methods and claims supported by the evidence?\n'
    '- "novelty": integer 1-5 — new versus prior work.\n'
    '- "significance": integer 1-5 — importance of the problem and result.\n'
    '- "reproducibility": integer 1-5 — code/data/detail sufficient to reproduce.\n'
    '- "clarity": integer 1-5 — how clearly it is written.\n'
    '- "confidence": number 0-1 — your confidence in this assessment.\n'
    "Single JSON object only, start {{ end }}."
)


def _digest_prompt(template: str, title: str, text: str, goals: str, focus_prompt: str) -> str:
    supplied_source_rule = (
        "The original source is already supplied. Acquisition and authentication are operational "
        "states owned by the caller, not scientific claims to invent in the digest. "
        "Review the supplied evidence; do not claim it was never retrieved. "
        "Source-stated limitations of external resources may still be reported faithfully.\n\n"
    )
    prompt = UNTRUSTED_INPUT_RULE + "\n\n" + supplied_source_rule + template.format(
        title=untrusted_input(title or "Untitled"), full_text=untrusted_input(text),
        research_goals=untrusted_input(goals),
    )
    if focus_prompt:
        prompt += (
            f"\n\nReader's focus note: {untrusted_input(focus_prompt)}\n"
            "Please adjust your review emphasis to highlight or downplay aspects matching this focus."
        )
    return prompt


def assess_digest(
    *, title: str, full_text: str, config: GoalsConfig, llm: Any, focus_prompt: str = "",
    max_chars: int | None = None, prefix: bool = False, response_format: dict[str, Any] | None = None,
    verifier_llm: Any = None, verification_text: str | None = None,
    verification_audit: dict[str, Any] | None = None, interpretation_out: dict[str, Any] | None = None,
) -> PaperDigest:
    """Grounded digest; prefix selects the A/B baseline and max_chars overrides its cap.

    response_format opts into decoder constraints; verification_text supplies original
    evidence for generated map notes. An explicit experimental interpretation_out selects
    C inside these same calls. It collects only an UNVERIFIED AI draft after successful
    base verification, never publishes a card or certifies its scientific statements.
    """
    from zotero_summarizer.services.library._source_admission import admit_source
    from zotero_summarizer.services.library._review_attempt import identity, record

    if interpretation_out is not None:
        interpretation_out.clear()
    attempt_out: dict[str, Any] | None = {} if interpretation_out is not None else None
    if verification_audit is not None:
        verification_audit.clear()
    record('config', identity(config.model_dump_json()), kind='identity')
    admit_source(full_text if verification_text is None else verification_text)
    template = config.prompts.paper_digest or _DEFAULT_DIGEST_PROMPT
    cap = int(max_chars if max_chars is not None else config.quality_review.max_text_chars)
    locations: list[dict[str, Any]] = []
    selection = {"locations": locations} if verification_audit is not None else {}
    text = full_text[:cap] if prefix else select_review_text([], full_text, budget=cap, **selection)
    source_text = text if verification_text is None else verification_text
    if prefix or verification_text is not None:
        locations = [{"selected_span": {"start": 0, "end": len(source_text)},
                      "original_span": {"start": 0, "end": len(source_text)}, "mapping": "exact"}]
    source = {"original_text": full_text if verification_text is None else verification_text,
              "selected_text": source_text, "locations": locations,
              "contracts": _digest_audit._contract_hashes() if verification_audit is not None else {}}
    record('generated_notes' if verification_text is not None else 'selected_original', text)
    if not text.strip() or not source_text.strip():
        raise ValueError("Digest assessment requires non-empty source text")
    goals = "; ".join(g for g in (config.research_goals or []) if str(g).strip()) or "(not specified)"
    prompt = _digest_prompt(template, title, text, goals, focus_prompt)
    response_model, prompt, extra = _digest_response_contract(prompt, response_format, interpretation_out)
    try:
        digest = _generate_digest(llm, prompt, response_model, stage='generator', extra=extra, interpretation_out=attempt_out)
    except ValueError:
        digest = _generate_digest(llm, prompt + _STRICT_RETRY_SUFFIX, response_model,
                                  stage='generator_parse_retry', extra=extra, interpretation_out=attempt_out)
    digest = digest.model_copy(update={"basis": "full_text"})
    verifier = verifier_llm or llm
    lineage = [{"digest_sha256": _digest_audit.canonical_hash(digest.model_dump(mode="json")),
                "generator_attempt": 0, "verifier_route": "primary"}]
    try:
        checks = _verify_generated_digest(digest, source_text, verifier, llm, goals, attempt=lineage[-1])
    except DigestVerifierUnavailable:
        raise
    except ValueError as exc:
        lineage[-1]["outcome"] = "support_not_established"
        correction_prompt = (prompt + "\n\nThe previous digest failed source verification: "
                             + str(exc) + "\nReturn a corrected, fully source-grounded JSON digest only.")
        digest = _generate_digest(llm, correction_prompt, response_model, stage='correction',
                                  extra=extra, interpretation_out=attempt_out).model_copy(update={"basis": "full_text"})
        lineage.append({"digest_sha256": _digest_audit.canonical_hash(digest.model_dump(mode="json")),
                        "generator_attempt": 1, "verifier_route": "primary"})
        checks = _verify_generated_digest(digest, source_text, verifier, llm, goals, attempt=lineage[-1])
    lineage[-1]["outcome"] = "accepted"
    if verification_audit is not None:
        verification_audit.update(_digest_audit.build_verification_audit(digest, checks, source, lineage))
    if interpretation_out is not None:
        interpretation_out.update(attempt_out)
    return digest
