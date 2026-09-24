"""Decision-ordered brief: verdict/diagnosis (flag reason inlined) → evidence-grade
gauge (one calibrated band, replacing the Rigor/Relevance chips) → goal board (the
home of per-goal relevance; fired+relevant cells are "stained" and tether their
summary to its quote) → self-explaining quality panel. The full paper body is NOT
embedded; the digest is collapsed."""
from __future__ import annotations

import re

from zotero_summarizer.services.library import _paper_read_brief as brief
from zotero_summarizer.services.library import _paper_read_html as h

CONTENT = {"title": "AgentClinic", "authors": "A. B.", "keywords": [], "n_pages": 12,
           "references_count": 40, "figures": [],
           "render_sections": [{"title": "Methods", "text": "We evaluate on a held-out cohort here."}]}
DIGEST = {"tldr": "A clinical agent benchmark.", "executive_summary": "Benchmarks clinical agents.",
          "grade": "B", "read_decision": "read", "key_findings": ["52% accuracy"], "methods": "MIMIC-IV",
          "verdict": "A solid clinical agent benchmark with caveats."}
QUALITY = {"quality_band": "neutral", "grade": "B", "confidence": 0.66, "passes_agreed": 2, "passes_total": 3,
           "rubric": {"external_validation": "yes", "ablation": "no", "uncertainty": "no"},
           "evidence": {"external_validation": "We evaluate on a held-out cohort here."},
           "red_flags": ["no ablation study"], "overstatements": []}
HIGHLIGHT_Q = {"quality_band": "highlight", "grade": "A", "passes_agreed": 3, "passes_total": 3,
               "rubric": {"external_validation": "yes", "uncertainty": "yes", "ablation": "yes",
                          "baselines": "yes", "dataset_provenance": "yes", "repro_detail": "yes",
                          "code_data_released": "yes"},
               "evidence": {}, "red_flags": [], "overstatements": ["'fully reproducible' only at temp 0"]}
FLAG_Q = {"quality_band": "flag", "grade": "C", "passes_agreed": 2, "passes_total": 3,
          "rubric": {"external_validation": "no", "uncertainty": "no", "ablation": "no"},
          "evidence": {}, "red_flags": ["near-perfect 99.2% metric with no leakage discussion"],
          "overstatements": []}
GOALS = [
    {"goal": "Multiagent systems in clinical research", "retrieval_state": "hit", "relevant": True,
     "score": 2.7, "summary": "A 24-role triage agent society.", "key_sections": ["Methods"],
     "supporting_quotes": ["We evaluate on a held-out cohort here."], "abstained": False},
    {"goal": "Agent autonomy and determinism", "retrieval_state": "miss", "relevant": False, "score": 0.4},
    {"goal": "Multimodal AI for clinics", "retrieval_state": "not_retrieved", "score": 0.0},
]


def test_board_renders_all_states_and_gauge():
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=GOALS)
    assert 'class="goal-board"' in html
    for state in ("state-hit", "state-miss", "state-not_retrieved"):
        assert state in html
    # the two ARR chips (RIGOR/RELEVANCE) collapsed into one evidence-grade gauge
    assert 'class="gauge"' in html and "gauge-needle" in html
    assert "2/3 passes" in html  # self-consistency, surfaced on the gauge
    # the fired+relevant goal "takes the stain"; miss/not-retrieved stay unstained
    assert "state-hit stained" in html and "unstained" in html


def test_gauge_needle_position_tracks_band():
    # the gauge OWNS band interpretation: a calibrated position, not a bare label
    flag = brief.brief_html(CONTENT, quality=FLAG_Q, goal_summaries=GOALS)
    hi = brief.brief_html(CONTENT, quality=HIGHLIGHT_Q, goal_summaries=GOALS)
    assert "left:18%" in flag and "left:85%" in hi


def test_board_absorbs_per_goal_summary_sections_and_quote():
    # The fired cell is now the single home of per-goal relevance: summary, the
    # sections to read, and the quote (behind a per-cell disclosure).
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=GOALS)
    assert "24-role triage agent society" in html
    assert "Read for you: Methods" in html
    assert "g-quote" in html and "held-out cohort" in html
    assert not hasattr(brief, "per_goal_html")  # the separate repeated section is gone


def test_goal_summaries_deduplicate_without_losing_goal_evidence():
    shared = "A multisite clinical study compares triage decisions across independent reader cohorts."
    goals = [
        {"goal": "Goal Alpha: clinical workflow", "retrieval_state": "hit", "relevant": True,
         "score": 2.7, "summary": f"{shared} {shared}", "key_sections": ["Methods"],
         "supporting_quotes": ["Shared evidence supports Goal Alpha and Goal Gamma.", "Alpha-specific evidence passage." ]},
        {"goal": "Goal Beta: model safety", "retrieval_state": "hit", "relevant": True,
         "score": 2.6, "summary": "A MULTISITE CLINICAL STUDY COMPARES TRIAGE DECISIONS ACROSS INDEPENDENT READER COHORTS!",
         "key_sections": ["Results"], "supporting_quotes": ["Beta-specific evidence passage."]},
        {"goal": "Goal Gamma: reader outcomes", "retrieval_state": "hit", "relevant": True,
         "score": 2.5, "summary": "A separate analysis measures reader agreement and reports calibrated outcomes for each clinical site.",
         "key_sections": ["Evaluation"], "supporting_quotes": ["Shared evidence supports Goal Alpha and Goal Gamma."]},
        {"goal": "Goal Delta: long-form result", "retrieval_state": "hit", "relevant": True,
         "score": 2.4, "summary": "Delta " + " ".join(f"finding{i}" for i in range(70)),
         "supporting_quotes": ["Delta-specific evidence passage."]},
        {"goal": "Goal Epsilon: late finding", "retrieval_state": "hit", "relevant": True,
         "score": 2.3, "summary": "An independent late summary is preserved for a fourth distinct interest.",
         "supporting_quotes": ["Epsilon-specific evidence passage."]},
        {"goal": "Goal Zeta: final finding", "retrieval_state": "hit", "relevant": True,
         "score": 2.2, "summary": "A final separate result belongs only to the fifth interest.",
         "supporting_quotes": ["Zeta-specific evidence passage."]},
    ]

    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=goals)
    visible_board = html.split('<details class="goal-summary-more">')[0]
    visible_summaries = re.findall(r'<li class="goal-summary-item">.*?</li>', visible_board, re.DOTALL)
    visible_text = [re.search(r'<p class="goal-summary-text">(.*?)</p>', item, re.DOTALL).group(1)
                    for item in visible_summaries]

    assert len(visible_summaries) <= 3
    assert sum(len(text.replace("…", "").split()) for text in visible_text) <= 90
    assert html.count(shared) == 1  # repeated sentence + punctuation/case variant are shown once
    assert "For: Goal Alpha: clinical workflow; Goal Beta: model safety" in html
    assert "Goal Gamma: reader outcomes" in html  # same quote does not merge distinct summaries
    assert "goal-summary-more" in html and "finding69" in html and "Goal Zeta: final finding" in html
    assert html.count("Shared evidence supports Goal Alpha and Goal Gamma.") == 2
    for evidence in ("Alpha-specific", "Beta-specific", "Delta-specific", "Epsilon-specific", "Zeta-specific"):
        assert evidence in html
    assert html.count('role="meter"') == len(goals)


def test_question_does_not_merge_into_an_asserted_goal_finding():
    goals = [
        {"goal": "Hypothesis", "retrieval_state": "hit", "relevant": True, "score": 2.0,
         "summary": "The drug is safe?"},
        {"goal": "Conclusion", "retrieval_state": "hit", "relevant": True, "score": 2.0,
         "summary": "The drug is safe."},
    ]
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=goals)
    assert "The drug is safe?" in html and "The drug is safe." in html
    assert "For: Hypothesis; Conclusion" not in html


def test_numeric_interval_cannot_merge_with_point_estimate():
    goals = [{"goal": f"Goal {i}", "retrieval_state": "hit", "relevant": True,
              "score": 2.0, "summary": text}
             for i, text in enumerate(("Hazard ratio 1.2.", "Hazard ratio 1–2."))]
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=goals)
    assert "Hazard ratio 1.2." in html and "Hazard ratio 1–2." in html
    assert "For: Goal 0; Goal 1" not in html


def test_casefold_duplicate_does_not_merge_distinct_quantitative_findings():
    goals = [
        {"goal": "Large cohort", "retrieval_state": "hit", "relevant": True, "score": 2.0,
         "summary": "N=120 participants completed follow-up."},
        {"goal": "Small cohort", "retrieval_state": "hit", "relevant": True, "score": 2.0,
         "summary": "n=120 participants completed follow-up."},
    ]

    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=goals)

    assert "N=120 participants completed follow-up." in html
    assert "n=120 participants completed follow-up." in html
    assert "For: Large cohort; Small cohort" not in html


def test_legacy_degraded_goal_data_retains_long_detail_inline():
    detail = "Legacy result: " + " ".join(f"observation{i}" for i in range(70))
    goals = [
        {"goal": "Legacy goal", "retrieval_state": "hit", "relevant": True,
         "score": 2.0, "summary": detail},
        {"goal": "Degraded goal", "retrieval_state": "hit", "relevant": True,
         "score": 2.0, "summary": "A separate degraded-data finding."},
    ]

    html = brief.brief_html(CONTENT, quality=None, goal_summaries=goals)

    assert detail in html
    assert "A separate degraded-data finding." in html
    assert "Legacy goal" in html and "Degraded goal" in html


def test_goal_summary_disclosure_is_keyboard_accessible_and_responsive():
    goals = [dict(GOALS[0], goal=f"Goal {i}", summary=f"Distinct conclusion {i}.") for i in range(4)]
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=goals)
    css = brief.brief_css()

    assert '<details class="goal-summary-more">' in html
    assert "<summary" in html and "summary:focus-visible" in css
    assert ".goal-summary-list" in css and "overflow-wrap:anywhere" in css
    assert "@media(max-width:600px){.goal-board{grid-template-columns:1fr}" in css


def test_flag_verdict_inlines_the_red_flag():
    html = brief.brief_html(CONTENT, digest=DIGEST, quality=FLAG_Q, goal_summaries=GOALS)
    assert "SKIM" in html and "evidence weak" in html
    assert "near-perfect 99.2% metric with no leakage discussion" in html  # visible in the verdict bar


def test_brief_keeps_idea_evidence_and_writing_separate():
    digest = {**DIGEST, "read_decision": "skim", "novelty": 5, "significance": 4,
              "writing_friction": "high", "writing_reasons": ["Taxonomy mixes axes."]}
    html = brief.brief_html(CONTENT, digest=digest, quality=QUALITY, goal_summaries=GOALS)
    assert "Idea: high" in html and "Evidence: NEUTRAL" in html and "Writing: high" in html


def test_digest_action_overrides_relevance_without_hiding_relevance():
    digest = {"read_decision": "skip", "read_why": "The digest captures the useful result.",
              "estimated_read_minutes": 4}
    html = brief.brief_html(CONTENT, digest=digest, quality=HIGHLIGHT_Q, goal_summaries=GOALS)
    assert "DIGEST IS ENOUGH · 4 MIN" in html and "digest captures" in html
    assert "HIGH RELEVANCE" in html and "DEEP-READ" not in html


def test_missing_digest_does_not_invent_skip_even_for_assessed_misses():
    misses = [{"goal": g["goal"], "retrieval_state": "miss", "relevant": False, "score": 0.1} for g in GOALS]
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=misses)
    assert "REVIEW" in html
    assert ">SKIP<" not in html


def test_quality_panel_is_self_explaining():
    html = brief.quality_panel_html(HIGHLIGHT_Q)
    assert "Rigorous enough to act on" in html              # plain-language band gloss
    assert "How we judged it" in html and "no citation counts" in html  # the method clause
    assert "3/3 passes agree" in html                        # self-consistency in words
    assert "HIGHLIGHT" in html and "≥6 grounded checks" in html  # legend
    assert "What earned it" in html and "rigor checks met" in html
    # the full 9-point rubric is present but behind a disclosure, with REAL questions
    assert "Show the full" in html and "EXTERNAL / held-out" in html


def test_quality_panel_flag_leads_with_red_flags():
    html = brief.quality_panel_html(FLAG_Q)
    assert "Read critically" in html
    assert "q-redflags" in html and "near-perfect 99.2%" in html  # loud callout
    assert "Why it sank" in html


def test_presentation_integrates_brief_no_sections_dump_no_cdn():
    html = h._render_presentation(CONTENT, DIGEST, QUALITY, GOALS)
    assert 'class="gauge"' in html and 'id="quality"' in html
    assert 'id="sections"' not in html and 'id="per-goal"' not in html  # the dumps are gone
    assert 'class="fade-in digest-fold"' in html  # digest collapsed by default
    assert "cdn.jsdelivr" not in html
    assert "READ" in html


def test_abstained_hit_shows_withheld_not_evidence_found():
    abst = [{"goal": "Multiagent systems", "retrieval_state": "hit", "relevant": True,
             "score": 2.0, "summary": None, "abstained": True}]
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=abst)
    assert "grounded summary withheld" in html and "evidence found" not in html


def test_brief_empty_without_data():
    assert brief.brief_html(CONTENT, quality=None, goal_summaries=None) == ""
    digest_only = brief.brief_html(CONTENT, digest={"read_decision": "skip"}, quality=None, goal_summaries=None)
    assert "OFF GOAL" not in digest_only and "Idea: not assessed" in digest_only and "fonts.googleapis" not in h._render_presentation(CONTENT, DIGEST, None, None)
