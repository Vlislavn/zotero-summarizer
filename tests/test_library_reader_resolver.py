from __future__ import annotations

import pytest

import zotero_summarizer.runtime as runtime
from zotero_summarizer.runtime import AppContext, RuntimeState, set_context
from zotero_summarizer.services.library.app_library_reader import AppLibraryReader
from zotero_summarizer.services.zotero.zotero import get_library_reader, resolve_reader_for_key
from zotero_summarizer.settings import Settings
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_item


@pytest.fixture(autouse=True)
def _restore_global_context():
    """These tests ``set_context`` to a tmp settings; restore the prior global context
    afterward so the leak can't break a later test file's ``settings()``/``config`` fixture
    (default alphabetical order hides it, an explicit order does not)."""
    prev = runtime._context
    yield
    runtime._context = prev


def test_get_library_reader_falls_back_to_app_reader(tmp_path):
    settings = Settings.load(project_root=tmp_path)
    set_context(AppContext(settings=settings, state=RuntimeState()))

    reader = get_library_reader()

    assert isinstance(reader, AppLibraryReader)


def test_get_library_reader_prefers_live_zotero_reader(tmp_path):
    settings = Settings.load(project_root=tmp_path)
    live_reader = object()
    state = RuntimeState(zotero_reader=live_reader)
    set_context(AppContext(settings=settings, state=state))

    assert get_library_reader() is live_reader


def test_resolve_reader_for_key_dispatches_feed_key_to_app_reader(tmp_path):
    """A stable_feed_key (un-materialized Today paper) must resolve via AppLibraryReader
    EVEN with a live Zotero reader present — else the render/detail/review paths 404 on it.
    A Zotero key still uses the live reader."""
    settings = Settings.load(project_root=tmp_path)
    live_reader = object()
    set_context(AppContext(settings=settings, state=RuntimeState(zotero_reader=live_reader)))

    feed_key = stable_feed_key_from_item({"doi": "10.1/abc", "guid": "", "link": ""})
    assert isinstance(resolve_reader_for_key(feed_key), AppLibraryReader)  # NOT the live reader
    assert resolve_reader_for_key("ABCD2345") is live_reader  # 8-char Zotero key → live
