"""University-access readiness composes capability and session state."""
from types import SimpleNamespace

from zotero_summarizer.services.library import university_access


def test_stale_login_marker_is_not_ready_without_browser_extra(monkeypatch, tmp_path):
    ua = SimpleNamespace(
        enabled=True, browser_profile_dir=str(tmp_path), login_url="", ezproxy_prefix="",
    )
    monkeypatch.setattr(
        university_access, "get_state",
        lambda: SimpleNamespace(app_state=SimpleNamespace(
            config=SimpleNamespace(university_access=ua),
        )),
    )
    monkeypatch.setattr(university_access.browser_fetch, "is_available", lambda: False)
    monkeypatch.setattr(university_access.browser_fetch, "is_logged_in", lambda _path: True)

    result = university_access.status()

    assert result["browser_available"] is False
    assert result["logged_in"] is False
