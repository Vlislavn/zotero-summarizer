"""Reading targets belong beside the existing action, not in its folded digest."""
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from tests._reading_contract import Document
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
    assessed = [{"goal": "Owned goal", "retrieval_state": "miss", "relevant": False}]
    html = renderer._render_presentation(CONTENT, digest, goal_summaries=assessed if decision == "skip" else [])

    for text in [*DIGEST["read_parts"], DIGEST["original_value"]]:
        assert text not in html
    if decision == "skip":
        assert "DIGEST IS ENOUGH · 0 MIN" in Document(html).root.text()
        assert DIGEST["skip_parts"][0] in html and DIGEST["read_why"] in html
    else:
        assert 'aria-label="Reading plan"' not in html
        assert "0 MIN" not in html and DIGEST["read_why"] not in html
        assert "Reading decision unavailable" in html


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


def _nodes(markup, **attrs):
    return [node for node in Document(markup).root.nodes()
            if all(node.attrs.get(key) == value for key, value in attrs.items())]


def test_unknown_goal_skip_is_withheld_without_stale_action_details():
    digest = {**deepcopy(DIGEST), "read_decision": "skip", "estimated_read_minutes": 0}
    original = deepcopy(digest)
    html = renderer._render_presentation(CONTENT, digest, goal_summaries=[])
    assert _nodes(html, **{"class": "v-word"})[0].text() == "REVIEW"
    assert _nodes(html, **{"aria-label": "Reading plan"}) == []
    for text in [DIGEST["read_why"], *DIGEST["read_parts"], *DIGEST["skip_parts"], DIGEST["original_value"], "0 MIN"]:
        assert text not in html
    assert digest == original


@pytest.mark.parametrize("decision", [None, "", "legacy"])
def test_unknown_direct_action_never_revives_saved_rationale_time_or_plan(decision):
    html = brief.brief_html(CONTENT, digest={**DIGEST, "read_decision": decision}, quality=None, goal_summaries=[])
    assert _nodes(html, **{"class": "v-word"})[0].text() == "REVIEW"
    assert _nodes(html, **{"aria-label": "Reading plan"}) == []
    assert "Reading decision unavailable" in html
    assert DIGEST["read_why"] not in html and "12 MIN" not in html


def test_plan_filters_only_blank_list_items_preserving_nonblank_literals_and_order():
    target, skip = '  §4 <methods> & results  ', '  Appendix A — notation  '
    digest = {**deepcopy(DIGEST), "read_parts": [" ", target, "\t", DIGEST["read_parts"][1]],
              "skip_parts": ["\n", skip, " "]}
    original = deepcopy(digest)
    root = ET.fromstring(brief.brief_html(CONTENT, digest=digest, quality=None, goal_summaries=[]))
    plan = _plan(root)
    assert [li.text for li in plan.findall(".//li")] == [target, DIGEST["read_parts"][1], skip]
    assert plan.find('.//ol[@aria-label="Read parts"]') is not None
    assert digest == original


def test_blank_only_skip_hints_do_not_create_an_empty_plan():
    html = brief.brief_html(CONTENT, digest={**DIGEST, "read_decision": "skip", "skip_parts": [" ", "\t", "\n"]},
                            quality=None, goal_summaries=[])
    assert _nodes(html, **{"aria-label": "Reading plan"}) == []
    assert DIGEST["read_parts"][0] not in html and DIGEST["original_value"] not in html


@pytest.mark.parametrize("minutes", [None, 0])
def test_time_omission_and_explicit_zero_remain_distinct(minutes):
    html = brief.brief_html(CONTENT, digest={**DIGEST, "estimated_read_minutes": minutes}, quality=None, goal_summaries=[])
    text = Document(html).root.text()
    assert ("Time not estimated" in text) is (minutes is None)
    assert ("0 MIN" in text) is (minutes == 0)


@pytest.mark.parametrize("friction,action", [("low", "READ"), ("high", "SKIM")])
def test_actual_policy_keeps_qualified_read_and_preserves_saved_capped_time(friction, action):
    digest = {**deepcopy(DIGEST), "read_decision": "read", "basis": "full_text", "novelty": 5,
              "writing_friction": friction, "estimated_read_minutes": 60}
    quality = {"basis": "full_text", "quality_band": "highlight", "grade": "A"}
    goals = [{"goal": "Owned goal", "retrieval_state": "hit", "relevant": True, "abstained": False, "score": 3}]
    original = deepcopy((digest, quality, goals))
    html = renderer._render_presentation(CONTENT, digest, quality, goals)
    assert _nodes(html, **{"class": "v-word"})[0].text() == action + " · 60 MIN"
    plan, = _nodes(html, **{"aria-label": "Reading plan"})
    assert all(target in plan.text() for target in digest["read_parts"])
    assert bool(_nodes(html, title="Saved reading-time estimate")) is (friction == "high")
    assert (digest, quality, goals) == original


def test_composed_literal_plan_does_not_inject_html_or_change_cache_bytes(tmp_path):
    import json
    target = '<script>alert("owned")</script> & §3'
    digest = {**deepcopy(DIGEST), "read_parts": [target], "original_value": "A < B & C."}
    quality = {"quality_band": "neutral", "basis": "abstract", "grade": "B"}
    goals = []
    source = {"digest": digest, "model_digest": deepcopy(digest), "quality": quality, "goal_summaries": goals}
    path = tmp_path / "owned-review.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    for _ in range(2):
        html = renderer._render_presentation(CONTENT, source["digest"], source["quality"], source["goal_summaries"])
        plan, = _nodes(html, **{"aria-label": "Reading plan"})
        assert target in plan.text() and digest["original_value"] in plan.text()
        assert not any(node.tag in {"script", "a"} for node in plan.nodes())
    assert path.read_bytes() == before
    assert json.loads(before) == source


def test_actual_pdf_builder_retains_saved_time_metadata_from_projected_cache(tmp_path, monkeypatch):
    from tests.test_paper_render import _make_pdf
    from zotero_summarizer.services.library import deep_review, paper_render
    from zotero_summarizer.services.library.review_fleet import propose
    source = {**deepcopy(DIGEST), "basis": "full_text", "read_decision": "read", "novelty": 5,
              "writing_friction": "high", "estimated_read_minutes": 60}
    cached = propose.project_review({"model_digest": source,
        "quality": {"basis": "full_text", "quality_band": "highlight", "grade": "A"},
        "goal_summaries": [{"goal": "Owned", "retrieval_state": "hit", "relevant": True, "abstained": False}]})
    assert cached["digest"]["read_decision"] == "skim" and cached["model_read_decision"] == "read"
    before = deepcopy(cached)
    monkeypatch.setattr(deep_review, "get_current_review", lambda key: cached)
    monkeypatch.setattr(paper_render, "_use_docling", lambda: False)
    path = tmp_path / "owned.pdf"
    _make_pdf(path)
    built = paper_render.build_paper_read_for_pdf(path, item_key="OWNED")
    html = (paper_render._paper_directory(path) / "paper_presentation.html").read_text(encoding="utf-8")
    assert built["audit"]["status"] == "passed"
    assert _nodes(html, **{"class": "v-word"})[0].text() == "SKIM · 60 MIN"
    time, = _nodes(html, title="Saved reading-time estimate")
    assert time.text() == "60 MIN" and cached == before
