"""No browser operation may materialize an unbounded response/PDF in the client."""
from contextlib import nullcontext
import socket
from types import SimpleNamespace

import pytest

from tests.test_browser_fetch_degrade import _Ctx, _Page, _PW, _Req, _Resp
from zotero_summarizer.integrations import browser_fetch


@pytest.mark.parametrize("mode", ["direct", "metadata", "fallback", "render"])
@pytest.mark.parametrize("oversize", [False, True])
def test_browser_reads_bounded_streams_and_closes_them(tmp_path, monkeypatch, mode, oversize):
    url = "https://paper.example/article"
    pdf_url = "https://paper.example/document"
    body = b"%PDF" + b"x" * (13 if oversize else 12)
    page = _Page(meta_url=pdf_url if mode == "metadata" else None)
    page._print_body = body
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
        if mode == "render":
            return browser_fetch.render_article_pdf(url, cache_dir=cache, max_bytes=16)
        return browser_fetch.fetch_pdf_via_browser(
            url, profile_dir=tmp_path / "profile", cache_dir=cache,
            max_bytes=16, render_fallback=mode == "fallback",
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

    browser_fetch._authenticate_proxy(cdp, event, proxy, attempted)
    browser_fetch._authenticate_proxy(cdp, event, proxy, attempted)

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
        browser_fetch._read_stream(cdp, "body", cap)

    assert calls[-1] == ("IO.close", {"handle": "body"})
    if cap < 0:
        assert len(calls) == 1
