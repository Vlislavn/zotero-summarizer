"""Opt-in real-browser article extraction checks against the synthetic local origin.

The article is only tens of kilobytes; the bounded case exercises the cap, not OOM behavior.
"""
import hashlib
import os

import pytest

from tests.test_browser_egress_live import (  # noqa: F401
    _ARTICLE_MARKER_PARTS,
    _ARTICLE_MARKERS,
    _ARTICLE_OVER_CAP_REPETITIONS,
    _ARTICLE_OVER_CAP_SENTENCE,
    _ARTICLE_OVER_CAP_TEXT,
    _ARTICLE_SOURCE_CAP_BYTES,
    _LIVE_BROWSER_TIMEOUT_SECS,
    _dynamic_article_page,
    cdp_commands,
    origin_lab,
)
from zotero_summarizer.integrations import browser_fetch


pytestmark = pytest.mark.skipif(
    os.environ.get("ZS_BROWSER_EGRESS_SMOKE") != "1", reason="opt-in local Chromium check",
)


def test_javascript_article_text_and_unicode_are_in_rendered_pdf(tmp_path, origin_lab, cdp_commands):
    article_url = "http://paper.example/article_dynamic"
    cache = tmp_path / "cache"
    assert len(_dynamic_article_page(_ARTICLE_MARKER_PARTS)) < _ARTICLE_SOURCE_CAP_BYTES
    result = browser_fetch.render_article_pdf(
        article_url, cache_dir=cache,
        timeout=_LIVE_BROWSER_TIMEOUT_SECS, max_bytes=_ARTICLE_SOURCE_CAP_BYTES,
    )

    assert result is not None
    article_snapshot_path = (
        cache / "article-snapshots" / f"{hashlib.sha256(article_url.encode('utf-8')).hexdigest()}.pdf"
    )
    assert result == article_snapshot_path
    assert article_snapshot_path.is_file()
    assert not browser_fetch._cache_path(article_url, cache).exists()
    pdf_bytes = result.read_bytes()
    assert pdf_bytes.startswith(b"%PDF")
    assert 0 < len(pdf_bytes) <= _ARTICLE_SOURCE_CAP_BYTES
    with pytest.importorskip("fitz").open(result) as document:
        text = " ".join(" ".join(page.get_text() for page in document).split())
    positions = [text.index(marker) for marker in _ARTICLE_MARKERS]
    assert positions == sorted(positions)
    assert any(path == "/article_dynamic" for path, _ in origin_lab)
    assert "Page.printToPDF" not in cdp_commands


def test_captured_pdf_magic_bypasses_small_text_budget_and_uses_raw_cache(
    tmp_path, origin_lab, cdp_commands, monkeypatch,
):
    article_url = "http://paper.example/gzip_pdf"
    source_pdf = b"%PDF" + b"x" * 4092
    cache = tmp_path / "cache"
    monkeypatch.setattr(browser_fetch, "article_text_limit", lambda _max_bytes: 8)
    writer_calls = []
    render_text_pdf = browser_fetch.render_text_pdf

    def record_text_writer(*args, **kwargs):
        writer_calls.append((args, kwargs))
        return render_text_pdf(*args, **kwargs)

    monkeypatch.setattr(browser_fetch, "render_text_pdf", record_text_writer)
    result = browser_fetch.render_article_pdf(
        article_url, cache_dir=cache,
        timeout=_LIVE_BROWSER_TIMEOUT_SECS, max_bytes=len(source_pdf),
    )

    raw_cache_path = cache / f"{hashlib.sha256(article_url.encode('utf-8')).hexdigest()[:16]}.pdf"
    article_snapshot_path = browser_fetch.article_snapshot_path(article_url, cache)
    assert result == raw_cache_path
    assert raw_cache_path.read_bytes() == source_pdf
    assert not article_snapshot_path.exists()
    assert writer_calls == []
    assert any(path == "/gzip_pdf" for path, _ in origin_lab)
    assert "Page.printToPDF" not in cdp_commands


def test_benign_article_over_source_cap_is_rejected_before_controlled_writer(
    tmp_path, origin_lab, cdp_commands, monkeypatch,
):
    overcap_page = _dynamic_article_page(
        [("", _ARTICLE_OVER_CAP_SENTENCE)], repeat_count=_ARTICLE_OVER_CAP_REPETITIONS,
    )
    overcap_bytes = len(_ARTICLE_OVER_CAP_TEXT.encode("utf-8"))
    assert len(overcap_page) < _ARTICLE_SOURCE_CAP_BYTES
    assert 20_000 <= overcap_bytes < 100_000

    fitz = pytest.importorskip("fitz")
    writer_calls = []
    open_pdf = fitz.open

    def controlled_text_writer(*args, **kwargs):
        writer_calls.append((args, kwargs))
        return open_pdf(*args, **kwargs)

    monkeypatch.setattr(fitz, "open", controlled_text_writer)
    monkeypatch.setattr(fitz, "Document", controlled_text_writer)
    cache = tmp_path / "cache"
    assert len(_ARTICLE_OVER_CAP_TEXT.encode("utf-8")) > _ARTICLE_SOURCE_CAP_BYTES

    with pytest.raises(ValueError, match="max_bytes"):
        browser_fetch.render_article_pdf(
            "http://paper.example/article_overcap", cache_dir=cache,
            timeout=_LIVE_BROWSER_TIMEOUT_SECS, max_bytes=_ARTICLE_SOURCE_CAP_BYTES,
        )

    assert any(path == "/article_overcap" for path, _ in origin_lab)
    assert writer_calls == []
    assert list(cache.iterdir()) == []
    assert "Page.printToPDF" not in cdp_commands


def test_chrome_channel_persistent_text_fallback_preserves_full_unicode_article(
    tmp_path, origin_lab, cdp_commands,
):
    profile = tmp_path / "chrome-profile"
    article_url = "http://paper.example/article_dynamic"
    cache = tmp_path / "cache"
    raw_publisher_path = browser_fetch._cache_path(article_url, cache)
    article_snapshot_path = (
        cache / "article-snapshots" / f"{hashlib.sha256(article_url.encode('utf-8')).hexdigest()}.pdf"
    )
    result = browser_fetch.fetch_pdf_via_browser(
        article_url,
        profile_dir=profile,
        cache_dir=cache,
        timeout=_LIVE_BROWSER_TIMEOUT_SECS,
        max_bytes=_ARTICLE_SOURCE_CAP_BYTES,
        headless=True,
        channel="chrome",
        render_fallback=True,
    )

    assert result == article_snapshot_path
    assert article_snapshot_path.is_file()
    assert not raw_publisher_path.exists()
    assert profile.is_dir()
    fitz = pytest.importorskip("fitz")
    with fitz.open(result) as document:
        text = " ".join(" ".join(page.get_text() for page in document).split())
        assert text == " ".join(_ARTICLE_MARKERS)
        assert document.metadata["title"] == "Text-only article conversion"
        assert document.metadata["creator"] == "Zotero Summarizer"

    requests = [path for path, _ in origin_lab if path not in {"/authorization", "/stream-sent"}]
    assert len(requests) > 0
    assert any(path == "/article_dynamic" for path in requests)
    assert not any(path == "/authorization" for path, _ in origin_lab)
    assert not any(path == "/private" for path, _ in origin_lab)
    assert cdp_commands
    assert cdp_commands.count("Page.printToPDF") == 0
