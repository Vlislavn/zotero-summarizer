"""The optional method/code block stays inside the existing refine artifact."""
from __future__ import annotations

import pytest

from zotero_summarizer.models import MethodAndCode, RefinedSummary, SummarizeResponse
from zotero_summarizer.services.triage.prompts import DEFAULT_REFINE_PROMPT
from zotero_summarizer.services.zotero.pending import build_triage_note_html


def _method() -> MethodAndCode:
    return MethodAndCode(
        what_it_does="Ranks grounded evidence.",
        what_is_new="Uses a compact verification pass.",
        how_it_works=["Retrieve spans", "Verify claims"],
        evaluation="Compared with BaseNet on Dataset-X using F1.",
        artifacts=["https://github.com/example/exact-repo"],
        how_i_could_use_it="Reuse the verifier in paper QA.",
    )


def test_method_and_code_is_optional_and_round_trips():
    assert RefinedSummary(executive_summary="No reusable artifact.").method_and_code is None
    refined = RefinedSummary(executive_summary="Useful method.", method_and_code=_method())
    restored = RefinedSummary.model_validate_json(refined.model_dump_json())
    assert restored.method_and_code == _method()


def test_method_artifacts_require_exact_absolute_urls():
    with pytest.raises(ValueError, match="absolute HTTP"):
        MethodAndCode(artifacts=["github.com/example/guessed"])
    assert _method().artifacts == ["https://github.com/example/exact-repo"]


def test_refine_prompt_requests_one_nullable_block_without_an_extra_call():
    for text in (
        '"method_and_code"', '"what_it_does"', '"what_is_new"',
        '"how_it_works"', '"evaluation"', '"artifacts"',
        '"how_i_could_use_it"', "Otherwise null", "Never invent",
    ):
        assert text in DEFAULT_REFINE_PROMPT


def test_triage_note_renders_supported_method_and_omits_null():
    base = dict(
        executive_summary="Summary.", relevance_score=4,
        triage_rationale="Relevant.",
    )
    assert "Method and code" not in build_triage_note_html("T", SummarizeResponse(**base))
    html = build_triage_note_html(
        "T", SummarizeResponse(**base, method_and_code=_method()),
    )
    for expected in ("Method and code", "Ranks grounded evidence", "exact-repo"):
        assert expected in html
