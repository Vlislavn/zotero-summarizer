"""Paper-type taxonomy + per-type, standard-grounded quality CHECKLISTS + the pure
coverage-grade function.

The deep-review quality engine used ONE empirical-ML rubric for every paper, so a
review/policy paper was flagged for "no ablation / no dataset split / no leakage
discussion" — criteria that simply do not apply to it. The fix is to detect the
paper TYPE first (``paper_type.py``) and judge it against the recognized
reporting/appraisal standard FOR THAT TYPE, as binary, quote-grounded yes/no/na
items, then derive a transparent COVERAGE grade (fraction of applicable items met)
rather than an unvalidated 1-5 LLM self-score.

Anti-fabrication: every checklist item names the standard it derives from and a
``source_url`` to the primary/EQUATOR page. The questions are our own paraphrase of
each standard's CHECKABLE expectations (verified against the sources during research,
2026-06); they are NOT claimed to be verbatim checklist text. ``test_paper_type_
checklists.py`` asserts every item carries a real source URL, which guards against
re-introducing the un-cited / fabricated criteria this module replaces.

Item keys are reused from the legacy rubric where they map 1:1 (``external_validation``,
``uncertainty``, ``ablation`` …) so existing evidence/grounding plumbing is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PaperType(str, Enum):
    """MECE classification by study design / contribution mode (not topic).

    The two ``GENERIC_*`` values are the safe SUPERTYPES the detector falls back to
    when it is not confident enough to pick a leaf type."""

    EMPIRICAL_ML = "empirical_ml"
    METHODS_SYSTEM = "methods_system"
    CLINICAL_PREDICTION = "clinical_prediction"
    DIAGNOSTIC_ACCURACY = "diagnostic_accuracy"
    RCT_AI = "rct_ai"
    DATASET_BENCHMARK = "dataset_benchmark"
    SYSTEMATIC_REVIEW = "systematic_review"
    NARRATIVE_REVIEW = "narrative_review"
    SURVEY = "survey"
    POSITION = "position"
    POLICY = "policy"
    THEORY = "theory"
    CASE_REPORT = "case_report"
    EDITORIAL = "editorial"
    GENERIC_EMPIRICAL = "generic_empirical"
    GENERIC_REVIEW = "generic_review"


class Family(str, Enum):
    """Criteria families — used to gate type-agnostic structural checks (e.g. the
    leakage red-flag and the ``rigor_absent`` rule only apply where EMP is active)."""

    EMP = "emp"          # empirical rigor: baselines, ablation, uncertainty, external validation
    CLIN = "clin"        # clinical validity: calibration, patient-level split, external cohort
    SYNTH = "synth"      # evidence synthesis: search strategy, risk-of-bias of included studies
    ARG = "arg"          # argument quality: thesis, evidence cited, scope of claims
    REPRO = "repro"      # reproducibility/artifacts: code/data, compute, provenance


@dataclass(frozen=True)
class ChecklistItem:
    """One binary, quote-grounded appraisal question derived from a named standard.

    ``critical`` items are make-or-break: a critical item answered other than "yes"
    caps the band, and critical items carry double weight in the coverage fraction.
    """

    key: str
    question: str
    standard: str
    source_url: str
    critical: bool = False


@dataclass(frozen=True)
class ChecklistSpec:
    families: tuple[Family, ...]
    standards: tuple[tuple[str, str], ...]  # (name, url) shown in the UI header
    items: tuple[ChecklistItem, ...]
    label: str = ""


# ---- source URLs (verified against primary / EQUATOR pages, 2026-06) ----------------
_TRIPOD = "https://www.tripod-statement.org/"
_PROBAST = "https://www.probast.org/"
_CLAIM = "https://pubs.rsna.org/doi/10.1148/ryai.240300"
_STARD_AI = "https://www.nature.com/articles/s41591-025-03953-8"
_QUADAS = "https://www.acpjournals.org/doi/10.7326/0003-4819-155-8-201110180-00009"
_CONSORT_AI = "https://www.nature.com/articles/s41591-020-1034-x"
_MI_CLAIM = "https://pmc.ncbi.nlm.nih.gov/articles/PMC7538196/"
_REFORMS = "https://arxiv.org/abs/2308.07832"
_NEURIPS = "https://neurips.cc/public/guides/PaperChecklist"
_LEAKAGE = "https://arxiv.org/abs/2207.07048"
_DATASHEETS = "https://cacm.acm.org/research/datasheets-for-datasets/"
_PRISMA = "https://www.bmj.com/content/372/bmj.n71"
_AMSTAR2 = "https://www.bmj.com/content/358/bmj.j4008"
_SANRA = "https://pmc.ncbi.nlm.nih.gov/articles/PMC6434870/"
_WACHSMUTH = "https://aclanthology.org/E17-1017/"
_CARE = "https://www.care-statement.org/"


# ---- reusable item groups -----------------------------------------------------------
def _empirical_items() -> tuple[ChecklistItem, ...]:
    return (
        ChecklistItem("baselines", "Are fair, current baselines compared (not only weak/outdated ones)?", "REFORMS", _REFORMS, critical=True),
        ChecklistItem("ablation", "Is there an ablation or component analysis isolating what drives the result?", "NeurIPS checklist", _NEURIPS),
        ChecklistItem("uncertainty", "Are results reported with uncertainty — CIs, error bars, multi-seed std, or significance tests?", "NeurIPS checklist", _NEURIPS, critical=True),
        ChecklistItem("external_validation", "Is the method evaluated on held-out / independent / out-of-distribution data, not just the training distribution?", "REFORMS", _REFORMS, critical=True),
        ChecklistItem("no_leakage", "Does the paper address train/test leakage (clean split, no preprocessing/feature-selection on test, deduplication, temporal split)?", "Kapoor & Narayanan leakage taxonomy", _LEAKAGE, critical=True),
        ChecklistItem("repro_detail", "Are architecture, training setup and compute described well enough to reproduce?", "ML Reproducibility Checklist", _NEURIPS),
        ChecklistItem("code_data_released", "Is code OR data released, with a concrete access path (URL/repo)?", "NeurIPS checklist", _NEURIPS),
        ChecklistItem("dataset_provenance", "Is dataset provenance stated — source, version, and license/access?", "REFORMS", _REFORMS),
    )


def _clinical_prediction_items() -> tuple[ChecklistItem, ...]:
    return (
        ChecklistItem("outcome_defined", "Is the predicted outcome and its time horizon clearly defined?", "TRIPOD+AI", _TRIPOD),
        ChecklistItem("sample_size", "Is the study size / number of events justified (separately for development and evaluation)?", "TRIPOD+AI", _TRIPOD),
        ChecklistItem("patient_level_split", "Is the train/test split at the PATIENT/subject level (no per-patient leakage across folds)?", "CLAIM / MI-CLAIM", _CLAIM, critical=True),
        ChecklistItem("calibration", "Is calibration reported (calibration plot / Brier / slope), not only discrimination (AUC)?", "TRIPOD+AI", _TRIPOD, critical=True),
        ChecklistItem("external_validation", "Is the model validated on a truly independent / external cohort, or its absence justified?", "TRIPOD+AI / PROBAST", _PROBAST, critical=True),
        ChecklistItem("subgroup_fairness", "Is performance reported across clinically meaningful subgroups (e.g. stage, histology, demographics)?", "TRIPOD+AI", _TRIPOD),
        ChecklistItem("missing_data", "Is the handling of missing data described?", "TRIPOD+AI", _TRIPOD),
        ChecklistItem("code_data_released", "Is study data and/or analytical code availability stated?", "TRIPOD+AI", _TRIPOD),
    )


def _diagnostic_accuracy_items() -> tuple[ChecklistItem, ...]:
    return (
        ChecklistItem("reference_standard", "Is the reference standard (ground truth) defined and appropriate?", "CLAIM 2024 / STARD-AI", _CLAIM, critical=True),
        ChecklistItem("patient_level_split", "Are data partitions disjoint at patient level (no leakage across train/val/test)?", "CLAIM 2024", _CLAIM, critical=True),
        ChecklistItem("accuracy_with_ci", "Are sensitivity/specificity (and AUC) reported WITH confidence intervals?", "STARD-AI", _STARD_AI, critical=True),
        ChecklistItem("external_validation", "Was external testing on an independent dataset/institution performed (or its absence justified)?", "CLAIM 2024", _CLAIM, critical=True),
        ChecklistItem("subgroup_fairness", "Is performance reported per dataset AND demographic/severity subgroup?", "CLAIM 2024", _CLAIM),
        ChecklistItem("error_analysis", "Is there a failure/error analysis (e.g. confusion matrix, misclassified cases)?", "CLAIM 2024", _CLAIM),
        ChecklistItem("selection_bias", "Was a consecutive/representative sample enrolled (avoiding case-control selection bias)?", "QUADAS-2", _QUADAS),
        ChecklistItem("code_data_released", "Is code/model/data availability stated?", "CLAIM 2024", _CLAIM),
    )


def _rct_items() -> tuple[ChecklistItem, ...]:
    return (
        ChecklistItem("registration", "Was the trial prospectively registered / is there a protocol?", "CONSORT-AI / SPIRIT-AI", _CONSORT_AI, critical=True),
        ChecklistItem("randomization", "Are randomization and allocation concealment described?", "CONSORT-AI", _CONSORT_AI, critical=True),
        ChecklistItem("ai_version", "Is the AI intervention version + input handling + human-AI interaction described?", "CONSORT-AI", _CONSORT_AI),
        ChecklistItem("itt_analysis", "Is an intention-to-treat analysis the primary analysis?", "CONSORT-AI", _CONSORT_AI, critical=True),
        ChecklistItem("blinding", "Is blinding of outcome assessment described (or its infeasibility justified)?", "CONSORT-AI", _CONSORT_AI),
        ChecklistItem("harms", "Are harms / adverse events attributable to the AI monitored and reported?", "CONSORT-AI", _CONSORT_AI),
        ChecklistItem("error_analysis", "Is an analysis of AI performance errors reported?", "CONSORT-AI", _CONSORT_AI),
    )


def _dataset_items() -> tuple[ChecklistItem, ...]:
    return (
        ChecklistItem("motivation", "Is the dataset's motivation and intended use documented?", "Datasheets for Datasets", _DATASHEETS),
        ChecklistItem("composition", "Is composition described (instances, labels, splits)?", "Datasheets for Datasets", _DATASHEETS, critical=True),
        ChecklistItem("collection", "Is the collection process described (who/how/when)?", "Datasheets for Datasets", _DATASHEETS),
        ChecklistItem("dataset_provenance", "Is provenance + license/access stated?", "Datasheets for Datasets", _DATASHEETS, critical=True),
        ChecklistItem("known_biases", "Are known biases / limitations of the data disclosed?", "Datasheets for Datasets", _DATASHEETS),
        ChecklistItem("contamination", "Is overlap/contamination with common pretraining/eval sets addressed?", "Kapoor & Narayanan", _LEAKAGE, critical=True),
        ChecklistItem("baselines", "Are baseline methods evaluated on the resource with a clear protocol?", "REFORMS", _REFORMS),
    )


def _systematic_review_items() -> tuple[ChecklistItem, ...]:
    return (
        ChecklistItem("registration", "Was the review protocol pre-registered (e.g. PROSPERO)?", "AMSTAR-2 (critical)", _AMSTAR2, critical=True),
        ChecklistItem("search_strategy", "Are the databases searched and the full search strategy reported?", "PRISMA 2020 / AMSTAR-2 (critical)", _PRISMA, critical=True),
        ChecklistItem("eligibility", "Are explicit inclusion/exclusion (eligibility) criteria stated?", "PRISMA 2020", _PRISMA),
        ChecklistItem("flow_diagram", "Is a PRISMA flow diagram (records identified→screened→included) present?", "PRISMA 2020", _PRISMA),
        ChecklistItem("rob_assessment", "Is risk of bias of the INCLUDED studies assessed with a named tool?", "AMSTAR-2 (critical)", _AMSTAR2, critical=True),
        ChecklistItem("synthesis_method", "Is the synthesis/meta-analytic method appropriate and described?", "AMSTAR-2 (critical)", _AMSTAR2, critical=True),
        ChecklistItem("publication_bias", "Is publication / small-study bias assessed?", "AMSTAR-2 (critical)", _AMSTAR2),
        ChecklistItem("excluded_justified", "Is a list of excluded studies with justification provided?", "AMSTAR-2 (critical)", _AMSTAR2),
    )


def _narrative_review_items() -> tuple[ChecklistItem, ...]:
    # SANRA — the correct lightweight tool for non-systematic reviews / surveys.
    return (
        ChecklistItem("importance", "Does it justify the article's importance to the readership?", "SANRA", _SANRA),
        ChecklistItem("aims", "Does it state concrete aims or formulate questions?", "SANRA", _SANRA, critical=True),
        ChecklistItem("search_described", "Is the literature search described (even if non-systematic)?", "SANRA", _SANRA, critical=True),
        ChecklistItem("referencing", "Are key statements appropriately referenced?", "SANRA", _SANRA),
        ChecklistItem("reasoning", "Is scientific reasoning / evidence level presented (not just assertion)?", "SANRA", _SANRA),
        ChecklistItem("endpoint_data", "Are relevant endpoint data presented appropriately?", "SANRA", _SANRA),
    )


def _argument_items() -> tuple[ChecklistItem, ...]:
    # Wachsmuth argument-quality dimensions — for position/policy/theory/editorial.
    return (
        ChecklistItem("thesis", "Is a clear thesis / position stated?", "Argument quality (Wachsmuth)", _WACHSMUTH, critical=True),
        ChecklistItem("evidence_cited", "Are the premises supported with cited evidence (not bare assertion)?", "Argument quality (Wachsmuth)", _WACHSMUTH, critical=True),
        ChecklistItem("counterarguments", "Are counterarguments / limitations addressed?", "Argument quality (Wachsmuth)", _WACHSMUTH),
        ChecklistItem("scope_match", "Does the scope of the claims match the evidence offered?", "Argument quality (Wachsmuth)", _WACHSMUTH),
    )


def _policy_items() -> tuple[ChecklistItem, ...]:
    return _argument_items() + (
        ChecklistItem("consensus_process", "Is the recommendation/consensus process described (stakeholders, method e.g. Delphi)?", "Consensus-reporting practice", _CONSORT_AI),
        ChecklistItem("conflicts", "Are conflicts of interest / funding disclosed?", "Consensus-reporting practice", _CONSORT_AI),
    )


def _theory_items() -> tuple[ChecklistItem, ...]:
    return (
        ChecklistItem("assumptions", "Are the assumptions of each formal result stated?", "NeurIPS checklist (theory)", _NEURIPS, critical=True),
        ChecklistItem("proofs", "Is a complete proof or proof sketch given for each claim?", "NeurIPS checklist (theory)", _NEURIPS, critical=True),
        ChecklistItem("thesis", "Is the contribution and its relation to prior work clear?", "Argument quality", _WACHSMUTH),
    )


def _case_report_items() -> tuple[ChecklistItem, ...]:
    return (
        ChecklistItem("patient_context", "Is the patient context (history, presentation) described?", "CARE", _CARE),
        ChecklistItem("timeline", "Is a timeline of the episode of care provided?", "CARE", _CARE),
        ChecklistItem("intervention_outcome", "Are the intervention and its outcomes described?", "CARE", _CARE, critical=True),
        ChecklistItem("consent", "Is informed consent / de-identification stated?", "CARE", _CARE),
        ChecklistItem("generalizability", "Are generalizability caveats acknowledged?", "CARE", _CARE),
    )


# ---- the type → checklist table -----------------------------------------------------
CHECKLISTS: dict[PaperType, ChecklistSpec] = {
    PaperType.EMPIRICAL_ML: ChecklistSpec(
        (Family.EMP, Family.REPRO), (("NeurIPS / REFORMS", _REFORMS), ("Leakage taxonomy", _LEAKAGE)),
        _empirical_items(), "Empirical ML"),
    PaperType.METHODS_SYSTEM: ChecklistSpec(
        (Family.EMP, Family.REPRO), (("NeurIPS / REFORMS", _REFORMS),),
        _empirical_items(), "Methods / systems"),
    PaperType.CLINICAL_PREDICTION: ChecklistSpec(
        (Family.CLIN, Family.EMP, Family.REPRO), (("TRIPOD+AI", _TRIPOD), ("PROBAST+AI", _PROBAST)),
        _clinical_prediction_items(), "Clinical prediction model"),
    PaperType.DIAGNOSTIC_ACCURACY: ChecklistSpec(
        (Family.CLIN, Family.EMP), (("CLAIM 2024", _CLAIM), ("STARD-AI", _STARD_AI), ("QUADAS-2", _QUADAS)),
        _diagnostic_accuracy_items(), "Diagnostic-accuracy / imaging"),
    PaperType.RCT_AI: ChecklistSpec(
        (Family.CLIN,), (("CONSORT-AI", _CONSORT_AI),),
        _rct_items(), "RCT of an AI intervention"),
    PaperType.DATASET_BENCHMARK: ChecklistSpec(
        (Family.REPRO, Family.EMP), (("Datasheets for Datasets", _DATASHEETS), ("Leakage taxonomy", _LEAKAGE)),
        _dataset_items(), "Dataset / benchmark"),
    PaperType.SYSTEMATIC_REVIEW: ChecklistSpec(
        (Family.SYNTH,), (("PRISMA 2020", _PRISMA), ("AMSTAR-2", _AMSTAR2)),
        _systematic_review_items(), "Systematic review / meta-analysis"),
    PaperType.NARRATIVE_REVIEW: ChecklistSpec(
        (Family.SYNTH, Family.ARG), (("SANRA", _SANRA),),
        _narrative_review_items(), "Narrative review"),
    PaperType.SURVEY: ChecklistSpec(
        (Family.SYNTH, Family.ARG), (("SANRA", _SANRA),),
        _narrative_review_items(), "Survey"),
    PaperType.POSITION: ChecklistSpec(
        (Family.ARG,), (("Argument quality (Wachsmuth)", _WACHSMUTH),),
        _argument_items(), "Position / perspective"),
    PaperType.POLICY: ChecklistSpec(
        (Family.ARG,), (("Argument quality (Wachsmuth)", _WACHSMUTH),),
        _policy_items(), "Policy / framework / guideline"),
    PaperType.THEORY: ChecklistSpec(
        (Family.ARG,), (("NeurIPS checklist (theory)", _NEURIPS),),
        _theory_items(), "Theory / analysis"),
    PaperType.CASE_REPORT: ChecklistSpec(
        (Family.CLIN,), (("CARE", _CARE),),
        _case_report_items(), "Case report"),
    PaperType.EDITORIAL: ChecklistSpec(
        (Family.ARG,), (("Argument quality (Wachsmuth)", _WACHSMUTH),),
        _argument_items(), "Editorial / commentary"),
    # Safe supertypes (detector fallback): the intersection-y generic forms.
    PaperType.GENERIC_EMPIRICAL: ChecklistSpec(
        (Family.EMP, Family.REPRO), (("NeurIPS / REFORMS", _REFORMS),),
        _empirical_items(), "Empirical (type uncertain)"),
    PaperType.GENERIC_REVIEW: ChecklistSpec(
        (Family.SYNTH, Family.ARG), (("SANRA", _SANRA),),
        _narrative_review_items(), "Review (type uncertain)"),
}


def spec_for(paper_type: PaperType | str | None) -> ChecklistSpec:
    """The checklist for a type; falls back to GENERIC_EMPIRICAL for unknown/None."""
    if isinstance(paper_type, str):
        try:
            paper_type = PaperType(paper_type)
        except ValueError:
            paper_type = PaperType.GENERIC_EMPIRICAL
    return CHECKLISTS.get(paper_type or PaperType.GENERIC_EMPIRICAL, CHECKLISTS[PaperType.GENERIC_EMPIRICAL])


# Letter cut-offs for the coverage fraction (clearly "derived from coverage", not LLM).
_GRADE_BANDS = ((0.8, "A"), (0.6, "B"), (0.4, "C"), (0.0, "D"))


@dataclass(frozen=True)
class Coverage:
    """Transparent, glassbox grade derived from checklist coverage (no LLM number)."""

    met: int
    applicable: int
    fraction: float
    grade: str
    band: str                       # flag | neutral | highlight  (matches the legacy band space)
    missing_critical: list[str] = field(default_factory=list)


def coverage_grade(
    spec: ChecklistSpec, rubric: dict[str, str], grounded_yes: set[str],
    *, has_red_flag: bool = False,
) -> Coverage:
    """Pure function: weighted % of APPLICABLE items met (N/A excluded), with critical
    items double-weighted. An item counts as MET only if its verdict is "yes" AND it is
    grounded (in ``grounded_yes``). A failed critical item or any red flag caps the band
    at ``flag``/``neutral`` regardless of the fraction — the existing asymmetric
    conservatism, now type-correct."""
    num = den = 0.0
    missing_critical: list[str] = []
    for item in spec.items:
        verdict = rubric.get(item.key, "na")
        if verdict == "na":
            continue  # not applicable to this paper → drops out of both sums
        weight = 2.0 if item.critical else 1.0
        den += weight
        met = verdict == "yes" and item.key in grounded_yes
        if met:
            num += weight
        elif item.critical:
            missing_critical.append(item.key)
    fraction = round(num / den, 3) if den else 0.0
    grade = next(letter for thresh, letter in _GRADE_BANDS if fraction >= thresh)
    if has_red_flag or missing_critical:
        band = "flag" if (has_red_flag or len(missing_critical) >= 2) else "neutral"
    elif fraction >= 0.8:
        band = "highlight"
    elif fraction >= 0.5:
        band = "neutral"
    else:
        band = "flag"
    met_count = sum(1 for it in spec.items if rubric.get(it.key) == "yes" and it.key in grounded_yes)
    applicable = sum(1 for it in spec.items if rubric.get(it.key, "na") != "na")
    return Coverage(met_count, applicable, fraction, grade, band, missing_critical)


__all__ = ["PaperType", "Family", "ChecklistItem", "ChecklistSpec", "CHECKLISTS",
           "spec_for", "Coverage", "coverage_grade"]
