from types import SimpleNamespace

from zotero_summarizer.services.library import _review_identity
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def test_review_identity_changes_with_pdf_goals_or_focus(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"version one")
    config = _default_goals_config()
    monkeypatch.setattr(_review_identity, "settings", lambda: SimpleNamespace(summary_timeout_seconds=30))

    def identity(cfg=config, focus=""):
        return _review_identity.build_review_identity(
            config=cfg, pdf_path=str(pdf), source_kind="library", focus_prompt=focus)

    original = identity()
    pdf.write_bytes(b"version two")
    assert identity()["source_sha256"] != original["source_sha256"]
    changed = config.model_copy(deep=True)
    changed.research_goals = ["different goal"]
    assert identity(changed)["generation_sha256"] != original["generation_sha256"]
    assert identity(focus="methods only")["generation_sha256"] != original["generation_sha256"]


def test_generation_identity_tracks_prompt_contract_text(monkeypatch):
    """Editing prompt/contract TEXT restales reviews (the model sees different prose)."""
    from zotero_summarizer.services.library import _quality_prompts

    _review_identity._prompt_contract_text.cache_clear()
    try:
        first = _review_identity._prompt_contract_text()
        monkeypatch.setattr(
            _quality_prompts, "QUALITY_RUBRIC_PROMPT",
            _quality_prompts.QUALITY_RUBRIC_PROMPT + " an extra rubric instruction")
        _review_identity._prompt_contract_text.cache_clear()
        second = _review_identity._prompt_contract_text()
        assert first["_quality_prompts"] != second["_quality_prompts"]
    finally:
        _review_identity._prompt_contract_text.cache_clear()


def test_generation_identity_ignores_nonprompt_source_edits(monkeypatch):
    """The #88 fix: editing logic that is NOT prompt text must not stale reviews,
    while editing that module's prompt TEXT still does."""
    from zotero_summarizer.services.library import _paper_section_summaries

    _review_identity._prompt_contract_text.cache_clear()
    try:
        first = _review_identity._prompt_contract_text()
        # A new helper function is code, not prompt text -> identity unchanged.
        monkeypatch.setattr(_paper_section_summaries, "_added_helper", lambda value: value, raising=False)
        _review_identity._prompt_contract_text.cache_clear()
        assert _review_identity._prompt_contract_text() == first
        # Editing the section-summary prompt TEXT does restale.
        monkeypatch.setattr(
            _paper_section_summaries, "_SECTION_SUMMARY_PROMPT",
            _paper_section_summaries._SECTION_SUMMARY_PROMPT + " summarise more tersely")
        _review_identity._prompt_contract_text.cache_clear()
        changed = _review_identity._prompt_contract_text()
        assert changed["_paper_section_summaries"] != first["_paper_section_summaries"]
    finally:
        _review_identity._prompt_contract_text.cache_clear()
