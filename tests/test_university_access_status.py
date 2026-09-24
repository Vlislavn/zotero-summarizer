"""University-access readiness composes capability and session state."""
from types import SimpleNamespace

import pytest

from zotero_summarizer.services.library import university_access


def test_stale_login_marker_is_not_ready_without_browser_extra(monkeypatch, tmp_path):
    monkeypatch.setattr(
        university_access, "settings",
        lambda: SimpleNamespace(data_dir=tmp_path.parent, browser_profile_dir=tmp_path / "profile"),
    )
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


def test_profile_override_is_resolved_and_contained_under_settings_data(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(
        university_access, "settings",
        lambda: SimpleNamespace(data_dir=data, browser_profile_dir=data / "browser_profile"),
    )

    assert university_access.profile_dir(SimpleNamespace(browser_profile_dir="browser-session")) == (
        data / "browser-session"
    )
    assert university_access.profile_dir(SimpleNamespace(browser_profile_dir=str(data / "custom"))) == (
        data / "custom"
    )
    assert university_access.profile_dir(SimpleNamespace(browser_profile_dir="")) == (
        data / "browser_profile"
    )


@pytest.mark.parametrize("override", ["../outside", "/tmp/outside-zs-data"])
def test_profile_override_rejects_paths_outside_settings_data(monkeypatch, tmp_path, override):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(
        university_access, "settings",
        lambda: SimpleNamespace(data_dir=data, browser_profile_dir=data / "browser_profile"),
    )

    with pytest.raises(ValueError, match="inside Settings.data_dir"):
        university_access.profile_dir(SimpleNamespace(browser_profile_dir=override))


def test_profile_override_rejects_symlink_escape(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (data / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        university_access, "settings",
        lambda: SimpleNamespace(data_dir=data, browser_profile_dir=data / "browser_profile"),
    )

    with pytest.raises(ValueError, match="inside Settings.data_dir"):
        university_access.profile_dir(SimpleNamespace(browser_profile_dir="linked/profile"))
