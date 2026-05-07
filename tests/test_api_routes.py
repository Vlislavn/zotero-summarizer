from __future__ import annotations

from zotero_summarizer.api.app import create_app


def test_app_uses_canonical_routes_only():
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/health" in paths
    assert "/api/summaries" in paths
    assert "/api/summaries/batch" in paths
    assert "/results" in paths

    assert "/health" not in paths
    assert "/summarize" not in paths
    assert "/batch_summarize" not in paths
    assert "/dashboard" not in paths
