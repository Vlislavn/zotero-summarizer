from types import SimpleNamespace

from zotero_summarizer.services.library import deep_review, quality_review
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


def test_feed_client_is_resolved_only_for_map_reduce(monkeypatch):
    config = _default_goals_config()
    provider = SimpleNamespace(
        is_local=True, thinking_on=True, lean_deep_review=False,
        structured_output=False, name="test-provider",
    )
    calls = []
    app = SimpleNamespace(
        app_state=SimpleNamespace(config=config),
        pdf_extractor=object(),
        resolve_stage_provider=lambda stage: provider,
        resolve_stage_client=lambda stage, **kwargs: calls.append(stage) or object(),
    )
    monkeypatch.setattr(deep_review, "get_state", lambda: app)
    monkeypatch.setattr(deep_review, "_load_prestige_context", lambda: ({}, None))
    monkeypatch.setattr(quality_review, "build_response_format", lambda model: None)
    from zotero_summarizer.services.zotero import zotero
    monkeypatch.setattr(zotero, "get_library_reader", lambda _app: object())

    deep_review._build_ctx()
    assert "feed" not in calls

    calls.clear()
    config.quality_review.chunk_strategy = "map_reduce"
    deep_review._build_ctx()
    assert calls.count("feed") == 1
