"""Historical comparator from bd5e267, not the current grading contract.

Frozen from services/library/_paper_type_checklists.py:298,313–347. The
function body and thresholds are unchanged; only the function name is explicit.
This test-only baseline does not reverify the historical paper annotations.
"""
from zotero_summarizer.services.library._paper_type_checklists import ChecklistSpec, Coverage

_GRADE_BANDS = ((0.8, "A"), (0.6, "B"), (0.4, "C"), (0.0, "D"))


def legacy_coverage_grade(
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
