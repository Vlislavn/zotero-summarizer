"""Acceptance contracts for bounded, text-based browser article PDFs.

The browser and filesystem stay fake; assertions inspect the produced PDF, byte
budgets, and recorded browser I/O rather than implementation wording.
"""
from __future__ import annotations

import base64
import hashlib
from contextlib import nullcontext
from importlib import import_module
from pathlib import Path

import fitz
import pytest

from zotero_summarizer.integrations import browser_fetch


ARTICLE = "\n".join(
    [
        "EARLY_SENTINEL",
        *[f"article paragraph {index}: evidence stays in the source" for index in range(40)],
        "MIDDLE_SENTINEL",
        *[f"article paragraph {index}: results stay in the source" for index in range(40, 80)],
        "LATE_SENTINEL",
    ]
)
_SENTINELS = ("EARLY_SENTINEL", "MIDDLE_SENTINEL", "LATE_SENTINEL")


def _article_pdf():
    return import_module("zotero_summarizer.integrations._article_pdf")


def _browser_article():
    return import_module("zotero_summarizer.integrations._browser_article")


class _NavigationResponse:
    def __init__(self, harness):
        self.harness = harness

    def body(self):
        self.harness.response_body_calls += 1
        return self.harness.html_body


class _BodyLocator:
    def __init__(self, page):
        self.page = page

    def inner_text(self, *args, **kwargs):
        return self.page.read_text()

    def text_content(self, *args, **kwargs):
        return self.page.read_text()

    def evaluate(self, *args, **kwargs):
        return self.page.read_text()


class _Page:
    def __init__(self, harness, context=None):
        self.harness = harness
        self.context = context if context is not None else _Context(harness)
        self.cdp = None

    def goto(self, url, **kwargs):
        if self.cdp is not None:
            self.cdp.navigate(url)
        return _NavigationResponse(self.harness)

    def locator(self, selector):
        return _BodyLocator(self)

    def inner_text(self, *args, **kwargs):
        return self.read_text()

    def evaluate(self, *args, **kwargs):
        return self.read_text()

    def read_text(self):
        if isinstance(self.harness.page_text, BaseException):
            raise self.harness.page_text
        return self.harness.page_text

    def query_selector(self, selector):
        return None

    def eval_on_selector_all(self, selector, script):
        return []

    def wait_for_load_state(self, *args, **kwargs):
        return None


class _CDP:
    def __init__(self, harness, page):
        self.harness = harness
        self.page = page
        self.callbacks = {}
        self.stream = b""
        self.offset = 0

    def on(self, event, callback):
        self.callbacks[event] = callback

    def send(self, method, params=None):
        self.harness.cdp_methods.append(method)
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main"}}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 1}
        if method == "Runtime.evaluate":
            text = self.harness.page_text
            if isinstance(text, BaseException):
                raise text
            status = "invalid_unicode" if any(0xD800 <= ord(char) <= 0xDFFF for char in text) else "complete"
            return {
                "result": {
                    "value": {
                        "text": "" if status == "invalid_unicode" else text,
                        "status": status,
                        "done": status in {"complete", "empty"},
                        "nodes": 2 if text else 1,
                    }
                }
            }
        if method == "Page.printToPDF":
            self.stream = _make_pdf(self.harness.page_text)
            self.offset = 0
            return {"stream": "article-pdf"}
        if method == "Fetch.takeResponseBodyAsStream":
            self.stream = self.harness.html_body
            self.offset = 0
            return {"stream": "article-response"}
        if method == "IO.read":
            end = self.offset + params["size"]
            chunk = self.stream[self.offset:end]
            self.offset += len(chunk)
            return {
                "data": base64.b64encode(chunk).decode("ascii"),
                "base64Encoded": True,
                "eof": self.offset == len(self.stream),
            }
        return {}

    def navigate(self, url):
        callback = self.callbacks.get("Fetch.requestPaused")
        if callback:
            callback(
                {
                    "requestId": "article-document",
                    "frameId": "main",
                    "resourceType": "Document",
                    "responseStatusCode": 200,
                    "responseHeaders": [{"name": "content-type", "value": self.harness.response_content_type}],
                }
            )

    def detach(self):
        return None


class _Context:
    def __init__(self, harness):
        self.harness = harness

    def new_page(self):
        return _Page(self.harness, self)

    def new_cdp_session(self, page):
        cdp = _CDP(self.harness, page)
        page.cdp = cdp
        self.harness.sessions.append(cdp)
        return cdp

    def close(self):
        return None

    def add_cookies(self, cookies):
        return None


class _Browser:
    def __init__(self, harness):
        self.harness = harness

    def new_context(self):
        return _Context(self.harness)

    def close(self):
        return None


class _Chromium:
    def __init__(self, harness):
        self.harness = harness

    def launch(self, **kwargs):
        return _Browser(self.harness)

    def launch_persistent_context(self, *args, **kwargs):
        return _Context(self.harness)


class _Playwright:
    def __init__(self, harness):
        self.chromium = _Chromium(harness)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Harness:
    def __init__(self, page_text):
        self.page_text = page_text
        self.html_body = b"<html><body>article text</body></html>"
        self.response_content_type = "text/html"
        self.cdp_methods = []
        self.response_body_calls = 0
        self.sessions = []


def _make_pdf(text):
    document = fitz.open()
    page = None
    for index, line in enumerate(text.splitlines()):
        if index % 48 == 0:
            page = document.new_page()
        page.insert_text((36, 36 + (index % 48) * 14), line, fontsize=9)
    result = document.tobytes()
    document.close()
    return result


def _install_browser(monkeypatch, page_text=ARTICLE):
    harness = _Harness(page_text)
    monkeypatch.setattr(browser_fetch, "public_browser_options", lambda *args, **kwargs: nullcontext({}))
    monkeypatch.setattr(browser_fetch, "validate_rss_url", lambda url: url)
    monkeypatch.setattr(
        browser_fetch,
        "_load_playwright",
        lambda: (lambda: _Playwright(harness), RuntimeError),
    )
    return harness


def _pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document)


def test_public_article_render_writes_complete_readable_text_without_native_print(tmp_path, monkeypatch):
    harness = _install_browser(monkeypatch)

    result = browser_fetch.render_article_pdf(
        "https://article.example/story", cache_dir=tmp_path, timeout=4.0, max_bytes=128_000
    )

    assert result is not None
    assert "Page.printToPDF" not in harness.cdp_methods, harness.cdp_methods
    assert harness.response_body_calls == 0
    output = _pdf_text(result)
    assert all(marker in output for marker in _SENTINELS)
    assert "".join(output.split()) == "".join(ARTICLE.split())
    assert result.read_bytes().startswith(b"%PDF-")
    assert harness.page_text == ARTICLE


def test_browser_fetch_article_fallback_uses_dom_text_without_native_print(tmp_path, monkeypatch):
    harness = _install_browser(monkeypatch)

    result = browser_fetch.fetch_pdf_via_browser(
        "https://article.example/story",
        profile_dir=tmp_path / "profile",
        cache_dir=tmp_path / "cache",
        timeout=4.0,
        max_bytes=128_000,
        render_fallback=True,
    )

    assert result is not None
    assert "Page.printToPDF" not in harness.cdp_methods, harness.cdp_methods
    assert harness.response_body_calls == 0
    output = _pdf_text(result)
    assert all(marker in output for marker in _SENTINELS)
    assert "".join(output.split()) == "".join(ARTICLE.split())


def test_overbudget_article_fails_before_text_writer_is_called(tmp_path, monkeypatch):
    harness = _install_browser(monkeypatch, "x" * 100_000)
    article_pdf = _article_pdf()
    writer_calls = []
    real_writer = article_pdf.render_text_pdf

    def record_writer(text, *, max_bytes):
        writer_calls.append((text, max_bytes))
        return real_writer(text, max_bytes=max_bytes)

    monkeypatch.setattr(article_pdf, "render_text_pdf", record_writer)

    with pytest.raises(ValueError):
        browser_fetch.render_article_pdf(
            "https://article.example/over-budget", cache_dir=tmp_path, max_bytes=256
        )

    assert writer_calls == []
    assert "Page.printToPDF" not in harness.cdp_methods, harness.cdp_methods
    assert harness.response_body_calls == 0


def test_collector_counts_utf8_bytes_and_accepts_exact_budget():
    text = "café\tmiddle\nLATE\u2028end"
    byte_budget = len(text.encode("utf-8"))
    page = _Page(_Harness(text))

    collected = _browser_article().collect_article_text(page, max_bytes=byte_budget, timeout=2.0)

    assert collected == text


def test_collector_rejects_one_ascii_byte_over_budget():
    text = "café\tmiddle\nLATE\u2028end"
    byte_budget = len(text.encode("utf-8"))
    page = _Page(_Harness(text + "!"))

    with pytest.raises(ValueError):
        _browser_article().collect_article_text(page, max_bytes=byte_budget, timeout=2.0)


def test_collector_rejects_giant_utf8_text_and_propagates_page_failure():
    collector = _browser_article().collect_article_text
    giant_page = _Page(_Harness("🧪" * 250_000))

    with pytest.raises(ValueError):
        collector(giant_page, max_bytes=1024, timeout=2.0)

    failed_page = _Page(_Harness(TimeoutError("page text unavailable")))
    with pytest.raises(TimeoutError, match="page text unavailable"):
        collector(failed_page, max_bytes=1024, timeout=0.25)


def test_collector_rejects_invalid_unicode_instead_of_replacing_it():
    page = _Page(_Harness("before\ud800after"))

    with pytest.raises(ValueError):
        _browser_article().collect_article_text(page, max_bytes=128, timeout=2.0)


def test_text_pdf_capacity_and_unicode_text_are_machine_readable():
    article_pdf = _article_pdf()
    capacity = 32_768
    limit = article_pdf.article_text_limit(capacity)
    assert isinstance(limit, int) and limit > 0

    text = "EARLY café marker\nMIDDLE Ω marker\nLATE marker"
    pdf = article_pdf.render_text_pdf(text, max_bytes=capacity)

    assert isinstance(pdf, bytes)
    assert len(pdf) <= capacity
    assert pdf.startswith(b"%PDF-")
    with fitz.open(stream=pdf, filetype="pdf") as document:
        output = "\n".join(page.get_text() for page in document)
    assert "".join(output.split()) == "".join(text.split())
    assert all(marker in output for marker in ("EARLY", "MIDDLE", "LATE"))


def test_text_pdf_rejects_output_capacity_smaller_than_a_pdf_header():
    with pytest.raises(ValueError):
        _article_pdf().render_text_pdf("text", max_bytes=4)


def test_render_failure_preserves_invalid_cache_cleans_temp_and_releases_lock(tmp_path, monkeypatch):
    harness = _install_browser(monkeypatch)
    article_pdf = _article_pdf()
    url = "https://article.example/failing-render"
    url_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cache_path = tmp_path / "article-snapshots" / f"{url_key}.pdf"
    cache_path.parent.mkdir(parents=True)
    publisher_cache_path = tmp_path / f"{url_key[:16]}.pdf"
    cache_path.write_bytes(b"invalid existing cache")
    original_cache = cache_path.read_bytes()
    real_writer = article_pdf.render_text_pdf

    def fail_writer(text, *, max_bytes):
        raise RuntimeError("writer failed")

    monkeypatch.setattr(article_pdf, "render_text_pdf", fail_writer)

    with pytest.raises(RuntimeError, match="writer failed"):
        browser_fetch.render_article_pdf(url, cache_dir=tmp_path, max_bytes=32_768)

    assert cache_path.read_bytes() == original_cache
    assert list(cache_path.parent.glob("*.tmp")) == []
    assert not publisher_cache_path.exists()

    monkeypatch.setattr(article_pdf, "render_text_pdf", real_writer)
    real_replace = browser_fetch.os.replace

    def fail_replace(source, destination):
        assert source.parent == destination.parent == cache_path.parent
        assert destination == cache_path
        assert source.is_file()
        assert source.read_bytes().startswith(b"%PDF-")
        raise OSError("atomic replace failed")

    monkeypatch.setattr(browser_fetch.os, "replace", fail_replace)
    with pytest.raises(OSError, match="atomic replace failed"):
        browser_fetch.render_article_pdf(url, cache_dir=tmp_path, max_bytes=32_768)

    assert cache_path.read_bytes() == original_cache
    assert list(cache_path.parent.glob("*.tmp")) == []
    assert not publisher_cache_path.exists()

    monkeypatch.setattr(browser_fetch.os, "replace", real_replace)
    result = browser_fetch.render_article_pdf(url, cache_dir=tmp_path, max_bytes=32_768)

    assert result == cache_path
    assert result.read_bytes().startswith(b"%PDF-")
    assert list(cache_path.parent.glob("*.tmp")) == []
    assert not publisher_cache_path.exists()

    harness.html_body = _make_pdf("direct PDF cache")
    harness.response_content_type = "application/pdf"
    direct_pdf = browser_fetch.fetch_pdf_via_browser(
        url,
        profile_dir=tmp_path / "profile",
        cache_dir=tmp_path,
        timeout=4.0,
        max_bytes=32_768,
    )

    assert direct_pdf is not None
    assert direct_pdf == publisher_cache_path
    assert direct_pdf != result
    assert direct_pdf.read_bytes() == harness.html_body
