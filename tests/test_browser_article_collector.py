"""Deterministic contracts for bounded browser article collection."""
from __future__ import annotations

import base64
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from zotero_summarizer.integrations import _browser_article, browser_fetch


class _CollectorCDP:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.calls = []

    def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main", "loaderId": "initial"}}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 73}
        if method == "Runtime.evaluate":
            expression = params["expression"]
            if ".cleanup()" in expression:
                return {"result": {"type": "boolean", "value": True}}
            if not self.chunks:
                raise AssertionError("unexpected article DOM chunk request")
            return {"result": {"type": "object", "value": self.chunks.pop(0)}}
        return {}

    def detach(self):
        self.calls.append(("detach", None))


class _CleanupFailureCDP(_CollectorCDP):
    def __init__(self, chunks, cleanup_error):
        super().__init__(chunks)
        self.cleanup_error = cleanup_error

    def send(self, method, params=None):
        if method == "Runtime.evaluate" and ".cleanup()" in params["expression"]:
            self.calls.append((method, params))
            raise self.cleanup_error
        return super().send(method, params)


class _NoAddNoteValueError(ValueError):
    add_note = None


class _CollectorContext:
    def __init__(self, chunks_by_session):
        self.chunks_by_session = list(chunks_by_session)
        self.sessions = []

    def new_cdp_session(self, _page):
        session = _CollectorCDP(self.chunks_by_session.pop(0))
        self.sessions.append(session)
        return session


class _Page:
    def __init__(self, chunks_by_session):
        self.context = _CollectorContext(chunks_by_session)


class _ResponseCDP:
    def __init__(self, body):
        self.body = body
        self.offset = 0
        self.calls = []
        self.callbacks = {}

    def on(self, event, callback):
        self.callbacks[event] = callback

    def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main", "loaderId": "initial"}}}
        if method == "Fetch.takeResponseBodyAsStream":
            return {"stream": "response"}
        if method == "IO.read":
            chunk = self.body[self.offset:self.offset + params["size"]]
            self.offset += len(chunk)
            return {"data": base64.b64encode(chunk).decode("ascii"), "base64Encoded": True,
                    "eof": self.offset == len(self.body)}
        return {}

    def detach(self):
        self.calls.append(("detach", None))


class _RenderPage:
    def __init__(self, context):
        self.context = context

    def goto(self, _url, **_kwargs):
        self.context.cdp.callbacks["Fetch.requestPaused"]({
            "requestId": "document",
            "frameId": "main",
            "resourceType": "Document",
            "responseStatusCode": 200,
            "responseHeaders": [{"name": "content-type", "value": "text/html"}],
        })


class _RenderContext:
    def __init__(self, body):
        self.cdp = _ResponseCDP(body)
        self.page = _RenderPage(self)

    def new_page(self):
        return self.page

    def new_cdp_session(self, _page):
        return self.cdp

    def close(self):
        pass


class _RenderBrowser:
    def __init__(self, context):
        self.context = context
        self.closed = False

    def new_context(self):
        return self.context

    def close(self):
        self.closed = True


class _RenderPlaywright:
    def __init__(self, body):
        self.context = _RenderContext(body)
        self.browser = _RenderBrowser(self.context)
        self.chromium = SimpleNamespace(launch=lambda **_kwargs: self.browser)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _chunk(text, *, status="complete", done=True, nodes=1):
    return {"text": text, "status": status, "done": done, "nodes": nodes}


def test_collector_counts_all_nodes_and_observes_changes_with_finite_config(monkeypatch):
    monkeypatch.setenv("ZS_BROWSER_ARTICLE_NODE_BUDGET", "17")
    page = _Page([[_chunk("article")]])

    assert browser_fetch.collect_article_text(page, max_bytes=100, timeout=2) == "article"

    expression = next(params["expression"] for method, params in page.context.sessions[0].calls
                      if method == "Runtime.evaluate" and ".cleanup()" not in params["expression"])
    assert "NodeFilter.SHOW_ALL" in expression
    assert "MutationObserver" in expression and "takeRecords" in expression
    assert expression.index("nodes += 1") < expression.index("node.nodeType !== Node.TEXT_NODE")
    assert "FILTER_REJECT" not in expression
    assert "17" in expression


@pytest.mark.parametrize(
    "message",
    ["article text exceeds max_bytes", "article DOM changed during text extraction"],
)
def test_primary_error_survives_cleanup_failure_and_chains_cause(monkeypatch, message):
    primary_error = _NoAddNoteValueError(message)
    cleanup_error = RuntimeError("reader cleanup failed")
    cdp = _CleanupFailureCDP([], cleanup_error)
    page = SimpleNamespace(context=SimpleNamespace(new_cdp_session=lambda _page: cdp))

    def raise_primary(*_args):
        raise primary_error

    monkeypatch.setattr(_browser_article, "_evaluate_chunk", raise_primary)

    with pytest.raises(_NoAddNoteValueError) as raised:
        browser_fetch.collect_article_text(page, max_bytes=100, timeout=2)

    assert raised.value is primary_error
    assert type(raised.value) is _NoAddNoteValueError
    assert str(raised.value) == message
    assert raised.value.__cause__ is cleanup_error
    assert [method for method, _params in cdp.calls].count("detach") == 1


@pytest.mark.parametrize(
    ("chunks", "max_bytes", "message"),
    [
        ([_chunk("four")], 3, "article text exceeds max_bytes"),
        ([_chunk("partial", status="mutated", done=False, nodes=2)], 100,
         "article DOM changed during text extraction"),
    ],
)
def test_budget_and_mutation_errors_remain_primary_when_cleanup_fails(chunks, max_bytes, message):
    cleanup_error = RuntimeError("reader cleanup failed")
    cdp = _CleanupFailureCDP(chunks, cleanup_error)
    page = SimpleNamespace(context=SimpleNamespace(new_cdp_session=lambda _page: cdp))

    with pytest.raises(ValueError) as raised:
        browser_fetch.collect_article_text(page, max_bytes=max_bytes, timeout=2)

    assert type(raised.value) is ValueError
    assert str(raised.value) == message
    assert raised.value.__cause__ is cleanup_error
    assert [method for method, _params in cdp.calls].count("detach") == 1


def test_cleanup_only_failure_is_raised_and_detaches_once():
    cleanup_error = RuntimeError("reader cleanup failed")
    cdp = _CleanupFailureCDP([_chunk("article")], cleanup_error)
    page = SimpleNamespace(context=SimpleNamespace(new_cdp_session=lambda _page: cdp))

    with pytest.raises(RuntimeError, match="reader cleanup failed") as raised:
        browser_fetch.collect_article_text(page, max_bytes=100, timeout=2)

    assert raised.value is cleanup_error
    assert [method for method, _params in cdp.calls].count("detach") == 1


def test_execution_context_rejects_boolean_ids():
    class _BooleanContextCDP:
        def send(self, method, _params=None):
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "main"}}}
            if method == "Page.createIsolatedWorld":
                return {"executionContextId": True}
            raise AssertionError(f"unexpected CDP method: {method}")

    with pytest.raises(ValueError, match="isolated DOM world"):
        _browser_article._execution_context(_BooleanContextCDP(), "collection")


def test_node_budget_override_has_a_finite_maximum(monkeypatch):
    monkeypatch.setenv("ZS_BROWSER_ARTICLE_NODE_BUDGET", "1000001")
    page = _Page([[_chunk("not reached")]])

    with pytest.raises(ValueError, match="node budget"):
        browser_fetch.collect_article_text(page, max_bytes=100, timeout=2)

    assert page.context.sessions == []


def test_mutation_fails_closed_and_repeated_collection_gets_a_fresh_world():
    page = _Page([
        [_chunk("partial", status="mutated", done=False, nodes=2)],
        [_chunk("fresh article", nodes=3)],
    ])

    with pytest.raises(ValueError, match="changed|mutation"):
        browser_fetch.collect_article_text(page, max_bytes=100, timeout=2)
    assert browser_fetch.collect_article_text(page, max_bytes=100, timeout=2) == "fresh article"

    worlds = [params["worldName"] for session in page.context.sessions
              for method, params in session.calls if method == "Page.createIsolatedWorld"]
    assert len(worlds) == 2 and worlds[0] != worlds[1]
    assert all(any(method == "Runtime.evaluate" and ".cleanup()" in params["expression"]
                   for method, params in session.calls) for session in page.context.sessions)


@pytest.mark.parametrize(
    ("item", "message"),
    [
        (_chunk("\ud800"), "invalid Unicode"),
        (_chunk("x" * 8193), "chunk"),
        (_chunk("ok", done=False), "done"),
        (_chunk("ok", nodes=True), "nodes"),
        (_chunk("ok", nodes="1"), "nodes"),
        ({"text": "ok", "status": "unknown", "done": True, "nodes": 1}, "status"),
    ],
)
def test_collector_rejects_invalid_utf8_and_malformed_chunk_contract(item, message):
    page = _Page([[item]])

    with pytest.raises(ValueError, match=message):
        browser_fetch.collect_article_text(page, max_bytes=20_000, timeout=2)


def test_collector_preserves_exact_valid_utf8_bytes():
    text = "café 🧬"
    page = _Page([[_chunk(text)]])

    collected = browser_fetch.collect_article_text(page, max_bytes=100, timeout=2)

    assert collected.encode("utf-8") == b"caf\xc3\xa9 \xf0\x9f\xa7\xac"


def test_source_budget_fails_before_text_pdf_renderer(monkeypatch):
    page = _Page([[_chunk("four")]])
    monkeypatch.setattr(browser_fetch, "article_text_limit", lambda _limit: 3)
    monkeypatch.setattr(browser_fetch, "render_text_pdf",
                        lambda *_args, **_kwargs: pytest.fail("over-budget text reached PDF renderer"))

    with pytest.raises(ValueError, match="max_bytes"):
        browser_fetch._article_text_pdf(page, max_bytes=100, timeout=2)


def test_ephemeral_article_intercepts_and_caps_decoded_document_before_render(tmp_path, monkeypatch):
    runtime = _RenderPlaywright(b"decoded-html-too-large")
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (lambda: runtime, RuntimeError))
    monkeypatch.setattr(browser_fetch, "public_browser_options", lambda *_args: nullcontext({}))
    monkeypatch.setattr(browser_fetch, "article_text_limit", lambda _limit: 8)
    monkeypatch.setattr(browser_fetch, "_article_text_pdf",
                        lambda *_args: pytest.fail("uncapped document reached article renderer"))
    monkeypatch.setattr(browser_fetch, "_write_cache",
                        lambda *_args: pytest.fail("failed acquisition reached cache"))

    with pytest.raises(ValueError, match="max_bytes"):
        browser_fetch.render_article_pdf("https://article.example/paper", cache_dir=tmp_path,
                                         timeout=2, max_bytes=100)

    methods = [method for method, _params in runtime.context.cdp.calls]
    assert "Fetch.enable" in methods
    assert "IO.close" in methods
    assert runtime.browser.closed


def test_ephemeral_document_at_decoded_cap_is_fulfilled_before_render(tmp_path, monkeypatch):
    runtime = _RenderPlaywright(b"12345678")
    monkeypatch.setattr(browser_fetch, "_load_playwright", lambda: (lambda: runtime, RuntimeError))
    monkeypatch.setattr(browser_fetch, "public_browser_options", lambda *_args: nullcontext({}))
    monkeypatch.setattr(browser_fetch, "article_text_limit", lambda _limit: 8)
    monkeypatch.setattr(browser_fetch, "_article_text_pdf", lambda *_args: b"%PDF-1.7\nrendered")

    result = browser_fetch.render_article_pdf(
        "https://article.example/exact-cap", cache_dir=tmp_path, timeout=2, max_bytes=100,
    )

    assert result is not None and result.read_bytes().startswith(b"%PDF-")
    read_sizes = [params["size"] for method, params in runtime.context.cdp.calls if method == "IO.read"]
    fulfill = next(params for method, params in runtime.context.cdp.calls if method == "Fetch.fulfillRequest")
    assert read_sizes == [4, 5]  # fixed PDF magic prefix, then text cap plus one detector byte
    assert base64.b64decode(fulfill["body"]) == b"12345678"


@pytest.mark.parametrize("timeout", [0, float("inf"), float("nan")])
def test_article_render_rejects_invalid_timeout_before_browser_launch(tmp_path, monkeypatch, timeout):
    monkeypatch.setattr(
        browser_fetch, "_load_playwright",
        lambda: pytest.fail("invalid timeout reached browser initialization"),
    )

    with pytest.raises(ValueError, match="timeout"):
        browser_fetch.render_article_pdf(
            "https://article.example/invalid-timeout", cache_dir=tmp_path, timeout=timeout, max_bytes=100,
        )


def test_node_budget_counts_non_text_nodes_from_the_whole_tree(monkeypatch):
    monkeypatch.setenv("ZS_BROWSER_ARTICLE_NODE_BUDGET", "2")
    page = _Page([[_chunk("complete-looking text", nodes=3)]])

    with pytest.raises(ValueError, match="node budget"):
        browser_fetch.collect_article_text(page, max_bytes=100, timeout=2)

    assert page.context.sessions[0].calls[-1] == ("detach", None)


def test_repeated_successful_collections_use_fresh_reader_contexts():
    page = _Page([[_chunk("first article")], [_chunk("second article")]])

    assert browser_fetch.collect_article_text(page, max_bytes=100, timeout=2) == "first article"
    assert browser_fetch.collect_article_text(page, max_bytes=100, timeout=2) == "second article"

    worlds = [
        params["worldName"]
        for session in page.context.sessions
        for method, params in session.calls
        if method == "Page.createIsolatedWorld"
    ]
    assert len(worlds) == 2 and worlds[0] != worlds[1]
    assert all(
        any(method == "Runtime.evaluate" and ".cleanup()" in params["expression"]
            for method, params in session.calls)
        for session in page.context.sessions
    )


def test_navigation_between_chunks_fails_closed_without_returning_prefix():
    page = _Page([[
        _chunk("prefix", status="more", done=False, nodes=2),
        _chunk("", status="mutated", done=False, nodes=2),
    ]])

    with pytest.raises(ValueError, match="changed|mutation"):
        browser_fetch.collect_article_text(page, max_bytes=100, timeout=2)

    expression = next(
        params["expression"]
        for method, params in page.context.sessions[0].calls
        if method == "Runtime.evaluate" and ".cleanup()" not in params["expression"]
    )
    assert "initialUrl = location.href" in expression
    assert "location.href !== initialUrl" in expression


def test_evaluation_timeout_after_prefix_never_returns_partial_text():
    class _TimeoutCDP(_CollectorCDP):
        def __init__(self):
            super().__init__([_chunk("prefix", status="more", done=False, nodes=2)])
            self.chunk_evaluations = 0

        def send(self, method, params=None):
            if method == "Runtime.evaluate" and ".cleanup()" not in params["expression"]:
                self.chunk_evaluations += 1
                if self.chunk_evaluations == 2:
                    self.calls.append((method, params))
                    raise TimeoutError("CDP evaluation timed out")
            return super().send(method, params)

    class _TimeoutContext(_CollectorContext):
        def new_cdp_session(self, _page):
            session = _TimeoutCDP()
            self.sessions.append(session)
            return session

    page = SimpleNamespace(context=_TimeoutContext([]))

    with pytest.raises(TimeoutError, match="timed out"):
        browser_fetch.collect_article_text(page, max_bytes=100, timeout=2)

    calls = page.context.sessions[0].calls
    assert any(method == "Runtime.evaluate" and ".cleanup()" in params["expression"]
               for method, params in calls)
    assert calls[-1] == ("detach", None)


def test_chunk_limit_accepts_exact_boundary_and_continues_without_splitting_utf8():
    boundary = "x" * _browser_article._TEXT_CHUNK_BYTES
    page = _Page([[
        _chunk(boundary, status="more", done=False, nodes=2),
        _chunk("🧬", nodes=3),
    ]])

    text = browser_fetch.collect_article_text(
        page, max_bytes=len(boundary.encode("utf-8")) + len("🧬".encode("utf-8")), timeout=2,
    )

    assert text == boundary + "🧬"


def test_proxy_auth_credentials_never_escape_exact_proxy_challenge():
    cdp = _ResponseCDP(b"")
    proxy = {"server": "http://127.0.0.1:8123", "username": "proxy-user", "password": "proxy-secret"}
    browser_fetch._install_response_capture(cdp, {"proxy": proxy}, max_bytes=100)
    authenticate = cdp.callbacks["Fetch.authRequired"]

    def challenge(request_id, origin, source="Proxy"):
        authenticate({
            "requestId": request_id,
            "authChallenge": {"source": source, "origin": origin},
        })

    challenge("exact", proxy["server"])
    challenge("exact", proxy["server"])
    challenge("origin", "https://article.example", source="Server")
    challenge("foreign", "http://127.0.0.1:9999")

    auth_calls = [params for method, params in cdp.calls if method == "Fetch.continueWithAuth"]
    assert auth_calls[0]["authChallengeResponse"] == {
        "response": "ProvideCredentials", "username": "proxy-user", "password": "proxy-secret",
    }
    assert all(call["authChallengeResponse"] == {"response": "CancelAuth"} for call in auth_calls[1:])
    assert all("proxy-secret" not in repr(call) for call in auth_calls[1:])
