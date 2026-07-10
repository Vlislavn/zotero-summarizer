"""Deep-review model tiering: the known-cheap sub-calls ride the light (feed)
client, the goal summaries stay on the strong deep_review model.

SOTA static per-identity tiering (claude-code `model-tier-routing`) — routing is
keyed on the identity of the sub-task, not a difficulty estimate. This exercises
``_deep_review_layers.extra_layers`` directly so it stays small + self-contained.
"""
from __future__ import annotations

import types

from zotero_summarizer.services.library import (
    _deep_review_layers,
    _paper_goal_summaries,
    _paper_section_summaries,
    paper_type,
    quality_eval,
)


def _cfg() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        quality_review=types.SimpleNamespace(
            use_docling=False, lean_self_consistency_runs=1, self_consistency_runs=3,
            lean_max_text_chars=1000, max_text_chars=5000, self_verification=True,
            shadow_claim_check=False, claim_check_model="x", batch_goal_summaries=True,
        ),
        research_goals=["goal A"],
    )


def test_extra_layers_routes_cheap_calls_to_light_llm(monkeypatch):
    strong, light = object(), object()  # identity sentinels — which client each layer got
    got: dict[str, object] = {}
    monkeypatch.setattr(paper_type, "detect_safe", lambda *a, **k: {"type": "generic_other"})
    monkeypatch.setattr(
        quality_eval, "evaluate_quality",
        lambda **kw: got.__setitem__("quality", kw["llm"]) or types.SimpleNamespace(model_dump=lambda: {}),
    )
    monkeypatch.setattr(
        _paper_section_summaries, "summarize_sections",
        lambda sections, llm: got.__setitem__("section", llm) or {},
    )
    monkeypatch.setattr(
        _paper_goal_summaries, "summarize_for_goals",
        lambda **kw: got.__setitem__("goals", kw["llm"]) or [],
    )

    ctx = _deep_review_layers.ExtraLayersCtx(
        item_key="K1", title="T", pdf_path="/x/p.pdf", text="BODY", digest_dump={},
        llm=strong, config=_cfg(), prestige=None, prestige_floor_value=None, llm_light=light,
    )
    _deep_review_layers.extra_layers(ctx)

    assert got["quality"] is light   # rubric / overstatement / self-verify → feed light
    assert got["section"] is light   # section one-liners → feed light
    assert got["goals"] is strong    # goal summaries stay on the strong model


def test_extra_layers_falls_back_to_strong_when_no_light(monkeypatch):
    """``llm_light=None`` (no distinct feed model) → cheap calls use the strong ``llm``."""
    strong = object()
    got: dict[str, object] = {}
    monkeypatch.setattr(paper_type, "detect_safe", lambda *a, **k: {"type": "generic_other"})
    monkeypatch.setattr(
        quality_eval, "evaluate_quality",
        lambda **kw: got.__setitem__("quality", kw["llm"]) or types.SimpleNamespace(model_dump=lambda: {}),
    )
    monkeypatch.setattr(_paper_section_summaries, "summarize_sections", lambda sections, llm: {})
    monkeypatch.setattr(_paper_goal_summaries, "summarize_for_goals", lambda **kw: [])

    ctx = _deep_review_layers.ExtraLayersCtx(
        item_key="K1", title="T", pdf_path="/x/p.pdf", text="BODY", digest_dump={},
        llm=strong, config=_cfg(), prestige=None, prestige_floor_value=None,  # llm_light defaults None
    )
    _deep_review_layers.extra_layers(ctx)

    assert got["quality"] is strong
