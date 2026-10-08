from types import SimpleNamespace

from zotero_summarizer.services.library import _review_identity
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def test_review_identity_changes_with_pdf_model_config_or_focus(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"version one")
    config = _default_goals_config()
    monkeypatch.setattr(_review_identity, "settings", lambda: SimpleNamespace(summary_timeout_seconds=30))
    monkeypatch.setattr(_review_identity, "_generation_sources", lambda: {"source.py": "a" * 64})

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


def test_generation_identity_tracks_analysis_extractor_code(tmp_path, monkeypatch):
    from zotero_summarizer.integrations import pdf as extractor
    source = tmp_path / "pdf.py"
    source.write_text("analysis extraction version one")
    monkeypatch.setattr(extractor, "__file__", str(source))
    _review_identity._generation_sources.cache_clear()
    try:
        first = _review_identity._generation_sources()
        source.write_text("analysis extraction version two")
        _review_identity._generation_sources.cache_clear()
        second = _review_identity._generation_sources()
        assert first["pdf.py"] != second["pdf.py"]
    finally:
        _review_identity._generation_sources.cache_clear()


def test_generation_identity_tracks_section_summary_verifier_code(tmp_path, monkeypatch):
    from zotero_summarizer.services.library import _paper_section_summaries

    source = tmp_path / "_paper_section_summaries.py"
    source.write_text("verifier version one")
    monkeypatch.setattr(_paper_section_summaries, "__file__", str(source))
    monkeypatch.setattr(_review_identity, "settings", lambda: SimpleNamespace(summary_timeout_seconds=30))
    config = _default_goals_config()
    _review_identity._generation_sources.cache_clear()
    try:
        first = _review_identity.build_review_identity(
            config=config, pdf_path="", source_kind="override", focus_prompt="",
        )
        source.write_text("verifier version two")
        _review_identity._generation_sources.cache_clear()
        second = _review_identity.build_review_identity(
            config=config, pdf_path="", source_kind="override", focus_prompt="",
        )

        assert first["generation_sha256"] != second["generation_sha256"]
    finally:
        _review_identity._generation_sources.cache_clear()
