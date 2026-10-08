"""Instruction-presence contracts, not semantic faithfulness evaluations."""

from zotero_summarizer.services.triage.prompts import DEFAULT_REFINE_PROMPT


SOURCE_STATUS_INSTRUCTION = (
    "SOURCE FIDELITY: Distinguish measured/observed results from retrospective "
    "simulations, projections, hypothetical or theoretical outcomes. Preserve "
    "conditional verbs, population/experiment scope, and attribution to the authors "
    "in each finding and overview, not only in methods. Do not present simulated or "
    "projected outcomes as observed events, or turn correlations into causal claims."
)


def test_default_refine_prompt_includes_global_source_status_instruction():
    instruction_at = DEFAULT_REFINE_PROMPT.index(SOURCE_STATUS_INSTRUCTION)
    assert instruction_at < DEFAULT_REFINE_PROMPT.index("Article metadata:")


def test_source_status_instruction_survives_prompt_rendering():
    rendered = DEFAULT_REFINE_PROMPT.format(
        output_language="English",
        current_date="2026-10-08",
        title="A generic experiment",
        doi="N/A",
        abstract="Measured results and projected outcomes.",
        paper_text="The authors report an experiment and a retrospective simulation.",
        research_goals="- Understand the contribution",
    )
    assert SOURCE_STATUS_INSTRUCTION in rendered
    assert "Start with { and end with }." in rendered
