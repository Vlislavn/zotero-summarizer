"""Section overlay: findings located onto the review's OWN sections (id join),
graceful degradation on page-sentinel / empty sections, index alignment."""
from __future__ import annotations

from zotero_summarizer.services.library import _review_section_overlay as overlay

SECTIONS = [
    {"id": "sec-1", "title": "Introduction", "level": 1, "page": 1,
     "text": "We study agentic triage of oncology literature using a learned gate over many RSS feeds."},
    {"id": "sec-2", "title": "Methods", "level": 1, "page": 4,
     "text": "The gate is trained on labelled abstracts and evaluated without any held-out external validation cohort."},
    {"id": "sec-3", "title": "Results", "level": 1, "page": 6,
     "text": "The system reaches a near-perfect AUROC of 0.99 on the internal split across all evaluation folds."},
]


def test_red_flag_quote_locates_to_its_section():
    quality = {
        "red_flags": ["near-perfect AUROC of 0.99 on the internal split across all evaluation folds"],
        "missing_critical": [],
        "evidence": {},
    }
    out = overlay.build_section_overlay(SECTIONS, quality, [])
    assert out["degraded"] is False
    assert len(out["red_flags"]) == 1                      # index-aligned to quality.red_flags
    assert out["red_flags"][0]["section"]["id"] == "sec-3"  # grounded in Results
    assert out["red_flags"][0]["section"]["page"] == 6


def test_quote_grounded_nowhere_is_unplaced_not_dropped():
    quality = {"red_flags": ["an entirely unrelated claim about quantum gravity wormhole metrics"],
               "missing_critical": [], "evidence": {}}
    out = overlay.build_section_overlay(SECTIONS, quality, [])
    assert len(out["red_flags"]) == 1                      # carried, never dropped
    assert out["red_flags"][0]["section"] is None          # but unplaced


def test_missing_critical_locates_via_its_evidence_quote():
    quality = {
        "red_flags": [],
        "missing_critical": ["external_validation"],
        "evidence": {"external_validation": "evaluated without any held-out external validation cohort"},
    }
    out = overlay.build_section_overlay(SECTIONS, quality, [])
    assert out["missing_critical"][0]["item"] == "external_validation"
    assert out["missing_critical"][0]["section"]["id"] == "sec-2"  # the Methods evidence span


def test_goal_locates_via_key_sections_title_then_quote_fallback():
    goals = [
        {"goal": "clinical agent eval", "key_sections": ["Methods"], "supporting_quotes": []},
        {"goal": "leaderboard chasing", "key_sections": [],
         "supporting_quotes": ["near-perfect AUROC of 0.99 on the internal split across all evaluation folds"]},
    ]
    out = overlay.build_section_overlay(SECTIONS, None, goals)
    assert [s["id"] for s in out["goals"][0]["sections"]] == ["sec-2"]   # exact title match
    assert [s["id"] for s in out["goals"][1]["sections"]] == ["sec-3"]   # quote fallback


def test_page_sentinel_sections_degrade_but_keep_findings_flat():
    page_sections = [
        {"id": "sec-1", "title": "Page 1", "level": 1, "page": 1, "text": "Body text of the first page here."},
        {"id": "sec-2", "title": "Page 2", "level": 1, "page": 2, "text": "Body text of the second page here."},
    ]
    quality = {"red_flags": ["some flag with at least six words present here"],
               "missing_critical": ["external_validation"], "evidence": {}}
    goals = [{"goal": "g", "key_sections": ["Page 1"], "supporting_quotes": []}]
    out = overlay.build_section_overlay(page_sections, quality, goals)
    assert out["degraded"] is True
    assert out["red_flags"][0]["text"] == "some flag with at least six words present here"
    assert out["red_flags"][0]["section"] is None          # no (mislabelled) anchor when degraded
    assert out["missing_critical"][0]["section"] is None
    assert out["goals"][0]["sections"] == []               # no anchors when degraded


def test_empty_sections_are_degraded():
    out = overlay.build_section_overlay([], {"red_flags": ["x"], "missing_critical": []}, [])
    assert out["degraded"] is True
    assert out["sections"] == []


def test_summaries_merge_onto_outline():
    out = overlay.build_section_overlay(SECTIONS, None, [], section_summaries={"sec-2": "How the gate is trained."})
    by_id = {s["id"]: s for s in out["sections"]}
    assert by_id["sec-2"]["summary"] == "How the gate is trained."
    assert by_id["sec-1"]["summary"] == ""                 # no summary → empty string


def test_exact_match_records_kind():
    quality = {"red_flags": ["near-perfect AUROC of 0.99 on the internal split across all evaluation folds"],
               "missing_critical": [], "evidence": {}}
    rf = overlay.build_section_overlay(SECTIONS, quality, [])["red_flags"][0]
    assert rf["section"]["id"] == "sec-3" and rf["match"] == "exact"


def test_two_tier_fallback_to_approx_section_when_no_span_grounds():
    # A red-flag DESCRIPTION (scattered tokens, not a contiguous span) that doesn't
    # ground but shares lexical content with Methods → coarse SECTION-LEVEL fallback
    # (CiteRead two-tier), marked `approx` so the UI renders it conservatively.
    quality = {"red_flags": ["external validation cohort gate held out split entirely absent"],
               "missing_critical": [], "evidence": {}}
    rf = overlay.build_section_overlay(SECTIONS, quality, [])["red_flags"][0]
    assert rf["section"]["id"] == "sec-2"                   # Methods, by overlap
    assert rf["match"] == "approx"                          # NOT exact/fuzzy — low-confidence anchor


def test_no_lexical_overlap_stays_unplaced_never_mislocated():
    quality = {"red_flags": ["quantum gravity wormhole metrics entirely unrelated nonsense here"],
               "missing_critical": [], "evidence": {}}
    rf = overlay.build_section_overlay(SECTIONS, quality, [])["red_flags"][0]
    assert rf["section"] is None and rf["match"] is None    # never mislocate when no real tie


def test_localization_stats_breakdown_for_calibration():
    quality = {
        "red_flags": [
            "near-perfect AUROC of 0.99 on the internal split across all evaluation folds",  # exact → sec-3
            "quantum gravity wormhole metrics entirely unrelated nonsense here",             # unplaced
        ],
        "missing_critical": ["external_validation"],
        "evidence": {"external_validation": "evaluated without any held-out external validation cohort"},  # exact → sec-2
    }
    stats = overlay.localization_stats(overlay.build_section_overlay(SECTIONS, quality, []))
    assert stats["exact"] == 2 and stats["unplaced"] == 1 and stats["total"] == 3
    assert abs(stats["located_rate"] - 2 / 3) < 1e-6


def test_localization_stats_zero_when_degraded():
    page_sections = [{"id": "sec-1", "title": "Page 1", "page": 1, "text": "body"}]
    quality = {"red_flags": ["x y z a b c d e f"], "missing_critical": [], "evidence": {}}
    stats = overlay.localization_stats(overlay.build_section_overlay(page_sections, quality, []))
    assert stats["total"] == 0 and stats["located_rate"] == 0.0
