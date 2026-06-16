"""Decision-ordered brief: verdict (flag reason inlined) → ARR spine → goal
board (now the home of per-goal relevance) → self-explaining quality panel.
The full paper body is NOT embedded; the digest is collapsed."""
from __future__ import annotations

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


def test_board_renders_all_states_and_spine():
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=GOALS)
    assert 'class="goal-board"' in html
    for state in ("state-hit", "state-miss", "state-not_retrieved"):
        assert state in html
    assert ">RIGOR<" in html and ">RELEVANCE<" in html
    assert "2/3 agree" in html  # self-consistency, not cryptic dots


def test_board_absorbs_per_goal_summary_sections_and_quote():
    # The fired cell is now the single home of per-goal relevance: summary, the
    # sections to read, and the quote (behind a per-cell disclosure).
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=GOALS)
    assert "24-role triage agent society" in html
    assert "Read for you: Methods" in html
    assert "g-quote" in html and "held-out cohort" in html
    assert not hasattr(brief, "per_goal_html")  # the separate repeated section is gone


def test_flag_verdict_inlines_the_red_flag():
    html = brief.brief_html(CONTENT, quality=FLAG_Q, goal_summaries=GOALS)
    assert "SKIM" in html and "FLAGGED" in html
    assert "near-perfect 99.2% metric with no leakage discussion" in html  # visible in the verdict bar


def test_no_fired_goal_is_skip():
    misses = [{"goal": g["goal"], "retrieval_state": "miss", "relevant": False, "score": 0.1} for g in GOALS]
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=misses)
    assert "SKIP" in html


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
    html = h._render_presentation(CONTENT, "AgentClinic", DIGEST, QUALITY, GOALS)
    assert 'class="spine"' in html and 'id="quality"' in html
    assert 'id="sections"' not in html and 'id="per-goal"' not in html  # the dumps are gone
    assert 'class="fade-in digest-fold"' in html  # digest collapsed by default
    assert "cdn.jsdelivr" not in html
    notes = h._render_notes(CONTENT, DIGEST, QUALITY, GOALS)
    assert "## Executive Summary" in notes and "## Relevance to your goals" in notes


def test_abstained_hit_shows_withheld_not_evidence_found():
    abst = [{"goal": "Multiagent systems", "retrieval_state": "hit", "relevant": True,
             "score": 2.0, "summary": None, "abstained": True}]
    html = brief.brief_html(CONTENT, quality=QUALITY, goal_summaries=abst)
    assert "grounded summary withheld" in html and "evidence found" not in html


def test_brief_empty_without_data():
    assert brief.brief_html(CONTENT, quality=None, goal_summaries=None) == ""
