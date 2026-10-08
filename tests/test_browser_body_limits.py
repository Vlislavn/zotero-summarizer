"""No browser operation may materialize an unbounded response/PDF in the client."""
import base64
import gzip
from contextlib import nullcontext
import socket
from types import SimpleNamespace

import pytest

from tests.test_browser_fetch_degrade import _Ctx, _Page, _PW, _Req, _Resp
from zotero_summarizer.integrations import _browser_response, browser_fetch


@pytest.mark.parametrize("mode", ["direct", "metadata"])
@pytest.mark.parametrize("oversize", [False, True])
def test_browser_reads_bounded_streams_and_closes_them(tmp_path, monkeypatch, mode, oversize):
    url = "https://paper.example/article"
    pdf_url = "https://paper.example/document"
    body = b"%PDF" + b"x" * (13 if oversize else 12)
    page = _Page(meta_url=pdf_url if mode == "metadata" else None)
    req = _Req({url: _Resp(body if mode == "direct" else b"<h1>x</h1>"), pdf_url: _Resp(body)})
    def forbidden(*args, **kwargs):
        pytest.fail("unbounded browser body API was used")
    req.get = page.pdf = forbidden
    ctx = _Ctx(req, page)
    pw = _PW(ctx)
    pw.chromium.launch = lambda **kw: SimpleNamespace(new_context=lambda: ctx, close=lambda: None)
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (lambda: pw, RuntimeError))
    monkeypatch.setattr(browser_fetch, "public_browser_options", lambda *a: nullcontext({}))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))])
    cache = tmp_path / "cache"

    def acquire():
        return browser_fetch.fetch_pdf_via_browser(
            url, profile_dir=tmp_path / "profile", cache_dir=cache, max_bytes=16,
        )

    if oversize:
        with pytest.raises(ValueError, match="exceeds max_bytes"):
            acquire()
        assert list(cache.glob("*.pdf")) == []
    else:
        assert acquire().read_bytes() == body
    methods = [method for method, _ in page._cdp.calls]
    assert methods.count("IO.close") == methods.count("Fetch.takeResponseBodyAsStream") + methods.count("Page.printToPDF")
    assert all(0 < params["size"] <= 17 for method, params in page._cdp.calls if method == "IO.read")


@pytest.mark.parametrize("source,origin,provide", [
    ("Proxy", "http://127.0.0.1:1234", True),
    ("Server", "http://127.0.0.1:1234", False),
    ("Proxy", "https://paper.example", False),
])
def test_proxy_secret_never_answers_origin_or_repeated_challenges(source, origin, provide):
    calls = []
    cdp = SimpleNamespace(send=lambda method, params: calls.append((method, params)))
    proxy = {"server": "http://127.0.0.1:1234", "username": "paper", "password": "test-only"}
    event = {"requestId": "one", "authChallenge": {"source": source, "origin": origin}}
    attempted = set()

    _browser_response.authenticate_proxy(cdp, event, proxy, attempted)
    _browser_response.authenticate_proxy(cdp, event, proxy, attempted)

    expected = {"response": "ProvideCredentials", "username": "paper", "password": "test-only"} if provide else {"response": "CancelAuth"}
    assert calls == [
        ("Fetch.continueWithAuth", {"requestId": "one", "authChallengeResponse": expected}),
        ("Fetch.continueWithAuth", {"requestId": "one", "authChallengeResponse": {"response": "CancelAuth"}}),
    ]


@pytest.mark.parametrize("result,cap,message", [
    (OSError("stream failed"), 16, "stream failed"),
    ({"data": "?", "base64Encoded": True, "eof": True}, 16, "base64"),
    ({"data": "", "eof": False}, 16, "no progress"),
    ({}, -1, "non-negative"),
])
def test_stream_errors_release_the_handle(result, cap, message):
    calls = []
    def send(method, params):
        calls.append((method, params))
        if method == "IO.read":
            if isinstance(result, Exception):
                raise result
            return result
    cdp = SimpleNamespace(send=send)

    with pytest.raises((ValueError, OSError), match=message):
        _browser_response.read_stream(cdp, "body", cap)

    assert calls[-1] == ("IO.close", {"handle": "body"})
    if cap < 0:
        assert len(calls) == 1


class _BrowserNavigationError(Exception):
    pass


class _ResponseCDP:
    def __init__(self):
        self.calls = []
        self.callbacks = {}
        self.stream = b""
        self.offset = 0
        self.aborted = False
        self.fulfilled = []

    def on(self, event, callback):
        wrapper = getattr(callback, "_pw_impl_instance_", None)
        if not wrapper:
            wrapper = lambda *args: callback(*args)
            setattr(callback, "_pw_impl_instance_", wrapper)
        self.callbacks[event] = wrapper

    def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main"}}}
        if method == "Fetch.takeResponseBodyAsStream":
            return {"stream": "response"}
        if method == "IO.read":
            chunk = self.stream[self.offset:self.offset + params["size"]]
            self.offset += len(chunk)
            return {"data": base64.b64encode(chunk).decode("ascii"), "base64Encoded": True,
                    "eof": self.offset == len(self.stream)}
        if method == "Fetch.failRequest":
            self.aborted = True
        if method == "Fetch.fulfillRequest":
            self.fulfilled.append(params)
        return {}

    def navigate(self, page, url):
        response = page.responses.get(url)
        if response is None:
            raise _BrowserNavigationError("navigation was blocked before a response")
        self.stream, self.offset, self.aborted = response["body"], 0, False
        self.callbacks["Fetch.requestPaused"]({
            "requestId": url,
            "frameId": "main",
            "resourceType": "Document",
            "responseStatusCode": response["status"],
            "responseHeaders": response["headers"],
        })
        if response.get("navigation_error"):
            error = response["navigation_error"]
            if isinstance(error, BaseException):
                raise error
            raise _BrowserNavigationError(error)
        if self.aborted or (response["status"] >= 400 and "application/pdf" in response["content_type"]):
            raise _BrowserNavigationError("Download is starting")


class _BrowserPage:
    def __init__(self, responses, *, meta_url=None, pdf_links=()):
        self.responses = responses
        self.meta_url = meta_url
        self.pdf_links = list(pdf_links)
        self.cdp = None

    def goto(self, url, **_kwargs):
        self.cdp.navigate(self, url)

    def query_selector(self, selector):
        if "citation_pdf_url" in selector and self.meta_url:
            return SimpleNamespace(get_attribute=lambda _name: self.meta_url)
        return None

    def eval_on_selector_all(self, _selector, _expression):
        return self.pdf_links


class _BrowserContext:
    def __init__(self, page):
        self.page = page
        self.cdp = _ResponseCDP()

    def new_page(self):
        self.page.cdp = self.cdp
        return self.page

    def new_cdp_session(self, _page):
        return self.cdp

    def add_cookies(self, _cookies):
        pass

    def close(self):
        pass


class _BrowserRuntime:
    def __init__(self, context):
        self.context = context
        self.browser = SimpleNamespace(new_context=lambda: context, close=lambda: None)
        self.chromium = SimpleNamespace(
            launch_persistent_context=lambda *_args, **_kwargs: context,
            launch=lambda **_kwargs: self.browser,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _response(status, content_type, body=b"", *, navigation_error=None):
    headers = [{"name": "content-type", "value": content_type}]
    if navigation_error:
        return {
            "status": status, "content_type": content_type, "headers": headers,
            "body": body, "navigation_error": navigation_error,
        }
    return {"status": status, "content_type": content_type, "headers": headers, "body": body}


def test_registered_error_callback_collects_the_exception_once():
    cdp = _ResponseCDP()
    capture = _browser_response.install_response_capture(
        cdp, {"proxy": {"server": "http://127.0.0.1:1", "username": "", "password": ""}},
        max_bytes=1_000, document_limit=100,
    )
    error = RuntimeError("CDP callback failed")

    cdp.callbacks["error"](error)

    assert len(capture.errors) == 1
    assert capture.errors[0] is error


def _capture_document(
    cdp, *, max_bytes, document_limit, status=200, headers=None, frame_id="main", resource_type="Document",
):
    capture = _browser_response.install_response_capture(
        cdp, {"proxy": {"server": "http://127.0.0.1:1", "username": "", "password": ""}},
        max_bytes=max_bytes, document_limit=document_limit,
    )
    captured, errors, pdf_misses = capture
    cdp.callbacks["Fetch.requestPaused"]({
        "requestId": "document", "frameId": frame_id, "resourceType": resource_type,
        "responseStatusCode": status,
        "responseHeaders": headers or [{"name": "content-type", "value": "text/html"}],
    })
    return captured, errors, pdf_misses


def _configure_runtime(monkeypatch, responses, *, meta_url=None, pdf_links=()):
    page = _BrowserPage(responses, meta_url=meta_url, pdf_links=pdf_links)
    context = _BrowserContext(page)
    runtime = _BrowserRuntime(context)
    monkeypatch.setattr(browser_fetch, "_load_playwright",
                        lambda: (lambda: runtime, _BrowserNavigationError))
    monkeypatch.setattr(browser_fetch, "public_browser_options", lambda *_args: nullcontext({}))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
    ])
    return runtime


@pytest.mark.parametrize("status", [401, 404])
def test_declared_non_success_pdf_status_is_recorded_and_native_viewer_aborted(status):
    cdp = _ResponseCDP()
    cdp.stream = b"private response body"
    captured, errors, misses = _capture_document(
        cdp, max_bytes=1_000, document_limit=100, status=status,
        headers=[{"name": "content-type", "value": "application/pdf"}],
    )

    assert captured == []
    assert errors == []
    assert misses == [status]
    assert ("Fetch.failRequest", {"requestId": "document", "errorReason": "Aborted"}) in cdp.calls
    assert not any(method == "Fetch.takeResponseBodyAsStream" for method, _ in cdp.calls)
    assert not cdp.fulfilled


@pytest.mark.parametrize("content_type", ["text/html", "application/pdf"])
def test_pdf_magic_uses_pdf_limit_independent_of_mime(content_type):
    pdf = b"%PDF" + b"x" * 100
    cdp = _ResponseCDP()
    cdp.stream = pdf

    captured, errors, misses = _capture_document(
        cdp, max_bytes=256, document_limit=8,
        headers=[{"name": "content-type", "value": content_type}],
    )

    assert captured == [pdf]
    assert errors == [] and misses == []
    assert ("Fetch.failRequest", {"requestId": "document", "errorReason": "Aborted"}) in cdp.calls
    assert ("IO.close", {"handle": "response"}) in cdp.calls


def test_pdf_mime_html_at_exact_document_limit_is_fulfilled():
    html = b"<h1>ok</h1>"
    cdp = _ResponseCDP()
    cdp.stream = html

    captured, errors, misses = _capture_document(
        cdp, max_bytes=256, document_limit=len(html),
        headers=[{"name": "content-type", "value": "application/pdf"}],
    )

    assert captured == [] and errors == [] and misses == []
    assert base64.b64decode(cdp.fulfilled[0]["body"]) == html
    assert ("IO.close", {"handle": "response"}) in cdp.calls


def test_pdf_mime_html_over_document_limit_fails_before_fulfill_and_closes_stream():
    html = b"<html>document exceeds the text cap</html>"
    document_limit = 8
    max_bytes = 256
    assert document_limit < len(html) < max_bytes
    cdp = _ResponseCDP()
    cdp.stream = html

    with pytest.raises(ValueError, match="exceeds max_bytes"):
        _capture_document(
            cdp, max_bytes=max_bytes, document_limit=document_limit,
            headers=[{"name": "content-type", "value": "application/pdf"}],
        )

    assert cdp.fulfilled == []
    assert ("Fetch.failRequest", {"requestId": "document", "errorReason": "Aborted"}) in cdp.calls
    assert cdp.calls.count(("IO.close", {"handle": "response"})) == 1


@pytest.mark.parametrize(("frame_id", "resource_type"), [("child", "Document"), ("main", "Image")])
def test_non_main_document_pdf_keeps_legacy_pdf_size_limit(frame_id, resource_type):
    pdf = b"%PDF" + b"x" * 100
    cdp = _ResponseCDP()
    cdp.stream = pdf

    captured, errors, misses = _capture_document(
        cdp, max_bytes=256, document_limit=8, frame_id=frame_id, resource_type=resource_type,
        headers=[{"name": "content-type", "value": "application/pdf"}],
    )

    assert captured == [pdf]
    assert errors == [] and misses == []
    assert ("IO.close", {"handle": "response"}) in cdp.calls


def test_decoded_gzip_html_within_text_limit_is_fulfilled_without_encoding_header():
    decoded_html = b"<h1>ok</h1>"
    encoded_length = len(gzip.compress(decoded_html))
    cdp = _ResponseCDP()
    cdp.stream = decoded_html  # Fetch supplies the decoded entity body.

    captured, errors, misses = _capture_document(
        cdp, max_bytes=1_000, document_limit=len(decoded_html),
        headers=[
            {"name": "content-type", "value": "text/html"},
            {"name": "content-encoding", "value": "gzip"},
            {"name": "content-length", "value": str(encoded_length)},
        ],
    )

    assert captured == [] and errors == [] and misses == []
    fulfilled = cdp.fulfilled[0]
    assert base64.b64decode(fulfilled["body"]) == decoded_html
    assert all(header["name"] not in {"content-encoding", "content-length"}
               for header in fulfilled["responseHeaders"])
    assert ("IO.close", {"handle": "response"}) in cdp.calls


def test_overcap_decoded_gzip_html_fails_before_fulfill_and_closes_stream():
    decoded_html = b"<html>decoded beyond text cap</html>"
    cdp = _ResponseCDP()
    cdp.stream = decoded_html  # Enforce the decoded size, not the smaller wire size.

    with pytest.raises(ValueError, match="exceeds max_bytes"):
        _capture_document(
            cdp, max_bytes=1_000, document_limit=8,
            headers=[
                {"name": "content-type", "value": "text/html"},
                {"name": "content-encoding", "value": "gzip"},
            ],
        )

    assert not cdp.fulfilled
    assert ("IO.close", {"handle": "response"}) in cdp.calls
    assert sum(method == "IO.close" for method, _ in cdp.calls) == 1
    sizes = [params["size"] for method, params in cdp.calls if method == "IO.read"]
    assert sizes and all(0 < size <= 9 for size in sizes)


@pytest.mark.parametrize("status", [401, 404])
def test_persistent_missing_pdf_tries_next_declared_pdf(tmp_path, monkeypatch, status):
    landing = "https://paper.example/article"
    missing = "https://paper.example/missing_pdf"
    healthy = "https://paper.example/healthy.pdf"
    pdf = b"%PDF-1.7\nhealthy"
    runtime = _configure_runtime(monkeypatch, {
        landing: _response(200, "text/html", b"<html>landing</html>"),
        missing: _response(status, "application/pdf"),
        healthy: _response(200, "application/pdf", pdf),
    }, meta_url=missing, pdf_links=[healthy])

    path = browser_fetch.fetch_pdf_via_browser(
        landing, profile_dir=tmp_path / "profile", cache_dir=tmp_path / "cache", max_bytes=1_000,
    )

    assert path is not None and path.read_bytes() == pdf
    assert ("Fetch.failRequest", {"requestId": missing, "errorReason": "Aborted"}) in runtime.context.cdp.calls
    assert ("Fetch.failRequest", {"requestId": healthy, "errorReason": "Aborted"}) in runtime.context.cdp.calls


@pytest.mark.parametrize("caller", ["persistent", "ephemeral"])
def test_known_pdf_absence_is_none_not_rendered_paywall(tmp_path, monkeypatch, caller):
    url = "https://paper.example/missing_pdf"
    runtime = _configure_runtime(monkeypatch, {url: _response(401, "application/pdf")})
    monkeypatch.setattr(browser_fetch, "_article_text_pdf",
                        lambda *_args: pytest.fail("declared missing PDF became a paywall render"))

    if caller == "persistent":
        result = browser_fetch.fetch_pdf_via_browser(
            url, profile_dir=tmp_path / "profile", cache_dir=tmp_path / "cache", max_bytes=1_000,
            render_fallback=True,
        )
    else:
        result = browser_fetch.render_article_pdf(url, cache_dir=tmp_path / "cache", max_bytes=1_000)

    assert result is None
    assert ("Fetch.failRequest", {"requestId": url, "errorReason": "Aborted"}) in runtime.context.cdp.calls


def test_all_missing_declared_pdfs_do_not_become_rendered_paywall(tmp_path, monkeypatch):
    landing = "https://paper.example/article"
    missing_a = "https://paper.example/missing_a.pdf"
    missing_b = "https://paper.example/missing_b.pdf"
    runtime = _configure_runtime(monkeypatch, {
        landing: _response(200, "text/html", b"<html>landing</html>"),
        missing_a: _response(404, "application/pdf"),
        missing_b: _response(401, "application/pdf"),
    }, meta_url=missing_a, pdf_links=[missing_b])
    monkeypatch.setattr(browser_fetch, "_article_text_pdf",
                        lambda *_args: pytest.fail("unavailable declared PDFs became a rendered paywall"))

    result = browser_fetch.fetch_pdf_via_browser(
        landing, profile_dir=tmp_path / "profile", cache_dir=tmp_path / "cache", max_bytes=1_000,
        render_fallback=True,
    )

    assert result is None
    assert ("Fetch.failRequest", {"requestId": missing_a, "errorReason": "Aborted"}) in runtime.context.cdp.calls
    assert ("Fetch.failRequest", {"requestId": missing_b, "errorReason": "Aborted"}) in runtime.context.cdp.calls


def test_prior_pdf_miss_does_not_suppress_unknown_later_navigation_error(tmp_path, monkeypatch):
    landing = "https://paper.example/article"
    missing = "https://paper.example/missing.pdf"
    blocked = "https://paper.example/blocked.pdf"
    runtime = _configure_runtime(monkeypatch, {
        landing: _response(200, "text/html", b"<html>landing</html>"),
        missing: _response(404, "application/pdf"),
        blocked: _response(
            200, "text/html", b"<html>blocked</html>", navigation_error="blocked navigation failed",
        ),
    }, meta_url=missing, pdf_links=[blocked])

    with pytest.raises(_BrowserNavigationError, match="blocked navigation failed"):
        browser_fetch.fetch_pdf_via_browser(
            landing, profile_dir=tmp_path / "profile", cache_dir=tmp_path / "cache", max_bytes=1_000,
        )

    assert ("Fetch.failRequest", {"requestId": missing, "errorReason": "Aborted"}) in runtime.context.cdp.calls
    assert any(response["requestId"] == blocked for response in runtime.context.cdp.fulfilled)


@pytest.mark.parametrize("caller", ["persistent", "ephemeral"])
@pytest.mark.parametrize("error_kind", ["browser", "transport"])
def test_unknown_navigation_error_still_propagates(tmp_path, monkeypatch, caller, error_kind):
    url = "https://paper.example/blocked"
    error = OSError("Download is starting") if error_kind == "transport" else "Download is starting"
    runtime = _configure_runtime(monkeypatch, {
        url: _response(200, "text/html", b"<html>ordinary response</html>", navigation_error=error),
    })

    if caller == "persistent":
        acquire = lambda: browser_fetch.fetch_pdf_via_browser(
            url, profile_dir=tmp_path / "profile", cache_dir=tmp_path / "cache", max_bytes=1_000,
        )
    else:
        acquire = lambda: browser_fetch.render_article_pdf(url, cache_dir=tmp_path / "cache", max_bytes=1_000)

    expected_error = OSError if error_kind == "transport" else _BrowserNavigationError
    with pytest.raises(expected_error, match="Download is starting"):
        acquire()

    assert runtime.context.cdp.fulfilled
