"""Reading targets belong beside the existing action, not in its folded digest."""
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from zotero_summarizer.services.library import _paper_read_brief as brief
from zotero_summarizer.services.library import _paper_read_html as renderer

CONTENT = {"title": "Synthetic paper", "figures": []}
DIGEST = {
    "read_decision": "skim", "read_why": "Compare the held-out protocol with our planned experiment.",
    "read_parts": ["§4.2 Held-out evaluation — inspect site splits", "Figure 3 — inspect failure cases"],
    "skip_parts": ["Appendix A — notation recap"], "estimated_read_minutes": 12,
    "original_value": "The site-split protocol and individual failure examples.",
    "tldr": "A synthetic evaluation method.", "key_findings": ["A retained synthetic finding."],
}


def _plan(root):
    return root.find('.//div[@aria-label="Reading plan"]')


def test_brief_puts_ordered_targets_outside_all_disclosures_without_mutation():
    digest = deepcopy(DIGEST)
    root = ET.fromstring(brief.brief_html(CONTENT, digest=digest, quality=None, goal_summaries=[]))
    plan = _plan(root)

    assert plan is not None
    assert plan.get("role") == "region"
    assert [li.text for li in plan.findall(".//ol/li")] == DIGEST["read_parts"]
    assert [li.text for li in plan.findall(".//ul/li")] == DIGEST["skip_parts"]
    assert DIGEST["original_value"] in "".join(plan.itertext())
    assert root.findall('.//details//div[@aria-label="Reading plan"]') == []
    assert "12 MIN" in "".join(root.itertext())
    assert digest == DIGEST


def test_presentation_has_one_home_for_reading_details_before_its_folded_digest():
    html = renderer._render_presentation(CONTENT, DIGEST)

    for text in [*DIGEST["read_parts"], *DIGEST["skip_parts"], DIGEST["read_why"], DIGEST["original_value"]]:
        assert html.count(text) == 1
        assert html.index(text) < html.index('id="digest"')


def test_legacy_optional_absence_is_unknown_not_an_estimate_or_fabricated_target():
    root = ET.fromstring(brief.brief_html(CONTENT, digest={
        "read_decision": "skim", "read_why": "Saved rationale.",
    }, quality=None, goal_summaries=[]))
    plan = _plan(root)

    assert "Time not estimated" in "".join(root.itertext())
    assert "Exact reading targets not recorded" in "".join(plan.itertext())
    assert "Additional value not recorded" in "".join(plan.itertext())
    assert plan.findall(".//li") == []
    assert "0 MIN" not in "".join(root.itertext())


@pytest.mark.parametrize("decision", ["skip", ""])
def test_skip_or_withheld_action_does_not_revive_inconsistent_legacy_read_advice(decision):
    digest = {**DIGEST, "read_decision": decision, "estimated_read_minutes": 0}
    html = renderer._render_presentation(CONTENT, digest)

    for text in [*DIGEST["read_parts"], DIGEST["original_value"]]:
        assert text not in html
    assert DIGEST["read_why"] in html
    if decision == "skip":
        assert "DIGEST IS ENOUGH · 0 MIN" in html
        assert DIGEST["skip_parts"][0] in html
    else:
        assert 'aria-label="Reading plan"' not in html
        assert "0 MIN" not in html


def test_abstract_projected_skim_is_not_promoted_by_renderer():
    root = ET.fromstring(brief.brief_html(CONTENT, digest={**DIGEST, "basis": "abstract"},
                                        quality=None, goal_summaries=[]))
    assert "SKIM · 12 MIN" in "".join(root.itertext())
    assert _plan(root) is not None


def test_read_targets_and_original_value_are_escaped_literal_text_not_navigation():
    target = '§3 <script>alert("source")</script> & methods'
    digest = {**DIGEST, "read_decision": "read", "read_parts": [target], "original_value": "A < B & C."}
    root = ET.fromstring(brief.brief_html(CONTENT, digest=digest, quality=None, goal_summaries=[]))
    plan = _plan(root)

    assert plan.find(".//ol/li").text == target
    assert "A < B & C." in "".join(plan.itertext())
    assert plan.findall(".//script") == []
    assert plan.findall(".//a") == []
