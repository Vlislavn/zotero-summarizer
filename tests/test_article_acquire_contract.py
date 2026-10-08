"""Caller acceptance for HTML article PDFs acquired by the library review service."""
from __future__ import annotations

import hashlib
import types

import pytest

from zotero_summarizer.integrations import browser_fetch, pdf_fetch
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import (
    _deep_review_layers,
    _digest_verification,
    _map_reduce,
    _pdf_acquire,
    _review_cache,
    deep_review,
    paper_type,
    review_detail,
)
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from zotero_summarizer.services.zotero import zotero as zotero_svc

ARTICLE_URL = "https://research.example.org/posts/controlled-article"


def _app(*, review_web_articles=True, browser_enabled=False):
    ua = types.SimpleNamespace(enabled=browser_enabled, fetch_timeout_secs=17.0, headless=True)
    qr = types.SimpleNamespace(
        max_pdf_bytes=321,
        fetch_timeout_secs=13.0,
        review_web_articles=review_web_articles,
    )
    config = types.SimpleNamespace(
        quality_review=qr,
        university_access=ua,
        prestige=types.SimpleNamespace(user_agent_email=""),
    )
    return types.SimpleNamespace(
        app_state=types.SimpleNamespace(config=config),
        unpaywall_client=None,
        openalex_cache=None,
        openalex_client=None,
    )


def _wire_acquisition(monkeypatch, app, cache_dir, *, render, online=True, resolve=None):
    monkeypatch.setattr(_pdf_acquire, "get_state", lambda: app)
    monkeypatch.setattr(
        _pdf_acquire, "settings", lambda: types.SimpleNamespace(pdf_cache_dir=cache_dir)
    )
    monkeypatch.setattr(_pdf_acquire, "offline_requested", lambda: not online)
    monkeypatch.setattr(pdf_fetch, "resolve_pdf_url", resolve or (lambda **_kwargs: None))
    monkeypatch.setattr(pdf_fetch, "fetch_pdf", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        browser_fetch, "fetch_pdf_via_browser",
        lambda *_args, **_kwargs: pytest.fail("web-article acquisition must not use the PDF browser rung"),
    )
    monkeypatch.setattr(browser_fetch, "render_article_pdf", render)
    monkeypatch.setattr(browser_fetch, "is_available", lambda: True)


def _expected_snapshot_path(url, cache_dir):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / "article-snapshots" / f"{key}.pdf"


def _expected_publisher_cache_path(url, cache_dir):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{key}.pdf"


def _controlled_pdf(cache_dir):
    path = _expected_snapshot_path(ARTICLE_URL, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\ncontrolled article body\n")
    return path


def test_article_result_preserves_url_flags_and_renderer_budget(monkeypatch, tmp_path):
    app = _app()
    path = _controlled_pdf(tmp_path)
    calls = []

    def render(url, **kwargs):
        calls.append((url, kwargs))
        return path

    _wire_acquisition(monkeypatch, app, tmp_path, render=render)

    result = _pdf_acquire.acquire_pdf_for("ARTICLE", {"url": ARTICLE_URL, "doi": ""})

    assert result.path == path
    assert result.source == "web_article" and result.source_url == ARTICLE_URL
    assert result.web_article is True and result.needs_login is False
    assert result.outcome == "acquired_web_article"
    assert calls == [(
        ARTICLE_URL,
        {"cache_dir": tmp_path, "timeout": 17.0, "max_bytes": 321},
    )]


def test_genuine_pdf_from_article_url_keeps_browser_provenance(monkeypatch, tmp_path):
    app = _app()
    cache = tmp_path / "cache"
    path = _expected_publisher_cache_path(ARTICLE_URL, cache)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"%PDF-1.7\npublisher PDF\n")
    _wire_acquisition(monkeypatch, app, cache, render=lambda *_args, **_kwargs: path)

    result = _pdf_acquire.acquire_pdf_for("ARTICLE", {"url": ARTICLE_URL, "doi": ""})

    assert result.path == path
    assert result.web_article is False
    assert result.source == "browser" and result.source_url == ARTICLE_URL
    assert result.outcome == "acquired_browser"


@pytest.mark.parametrize("is_snapshot", [False, True], ids=["publisher-pdf", "text-snapshot"])
def test_scholarly_browser_provenance_tracks_exact_snapshot_path(monkeypatch, tmp_path, is_snapshot):
    app = _app(browser_enabled=True)
    cache = tmp_path / "cache"
    path = (
        _expected_snapshot_path(ARTICLE_URL, cache)
        if is_snapshot else _expected_publisher_cache_path(ARTICLE_URL, cache)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\nacquired browser bytes\n")
    _wire_acquisition(monkeypatch, app, cache, render=lambda *_args, **_kwargs: None)
    calls = []
    monkeypatch.setattr(_pdf_acquire, "_profile_dir", lambda _ua: tmp_path / "profile")
    monkeypatch.setattr(browser_fetch, "fetch_pdf_via_browser", lambda url, **kwargs: calls.append((url, kwargs)) or path)

    result = _pdf_acquire.acquire_pdf_for(
        "ARTICLE", {"url": ARTICLE_URL, "doi": "10.5555/controlled"},
    )

    assert result.path == path
    assert result.web_article is is_snapshot
    assert result.source == ("web_article" if is_snapshot else "browser")
    assert result.source_url == ARTICLE_URL
    assert result.outcome == ("acquired_web_article" if is_snapshot else "acquired_browser")
    assert calls == [(ARTICLE_URL, {
        "profile_dir": tmp_path / "profile", "cache_dir": cache,
        "timeout": 17.0, "max_bytes": 321, "headless": True,
        "cookie_browser": "", "channel": "", "render_fallback": True,
    })]


def test_failed_article_render_reports_fetch_failure(monkeypatch, tmp_path):
    app = _app()
    _wire_acquisition(monkeypatch, app, tmp_path, render=lambda *_args, **_kwargs: None)

    result = _pdf_acquire.acquire_pdf_for("ARTICLE", {"url": ARTICLE_URL, "doi": ""})

    assert result.path is None
    assert result.outcome == "fetch_failed"
    assert result.needs_login is False and result.login_url == ""


@pytest.mark.parametrize(
    ("online", "allow_browser", "expected"),
    [(False, True, "offline_uncached"), (True, False, "browser_not_attempted")],
)
def test_article_render_obeys_offline_and_browser_policy(
    monkeypatch, tmp_path, online, allow_browser, expected,
):
    app = _app()
    calls = []
    _wire_acquisition(
        monkeypatch, app, tmp_path,
        render=lambda *args, **kwargs: calls.append((args, kwargs)), online=online,
    )

    result = _pdf_acquire.acquire_pdf_for(
        "ARTICLE", {"url": ARTICLE_URL, "doi": ""}, allow_browser=allow_browser,
    )

    assert result.path is None and result.outcome == expected
    assert calls == []


def test_declared_pdf_url_never_uses_article_renderer(monkeypatch, tmp_path):
    app = _app()
    calls = []
    pdf_url = "https://research.example.org/papers/controlled.pdf"
    _wire_acquisition(
        monkeypatch, app, tmp_path,
        render=lambda *args, **kwargs: calls.append((args, kwargs)),
        resolve=lambda **_kwargs: pdf_url,
    )

    result = _pdf_acquire.acquire_pdf_for("ARTICLE", {"url": pdf_url, "doi": ""})

    assert result.path is None and result.outcome == "fetch_failed"
    assert calls == []


@pytest.fixture(autouse=True)
def _clear_review_jobs():
    with deep_review._LOCK:
        deep_review._JOBS.clear()
    yield
    with deep_review._LOCK:
        deep_review._JOBS.clear()


def _prepare_review_context(monkeypatch, tmp_path, *, render, extract_text):
    config = _default_goals_config()
    config.quality_review.review_web_articles = True
    detail = {
        "title": "Controlled article", "url": ARTICLE_URL, "doi": "", "abstract": "",
        "pdf_path": "", "has_pdf": False, "item_type": None,
    }
    reader = types.SimpleNamespace(get_item_detail=lambda _key: detail)
    extractor = types.SimpleNamespace(extract_text=extract_text)
    provider = types.SimpleNamespace(
        is_local=True, lean_deep_review=True, thinking_on=False, structured_output=False,
        name="test-provider",
    )
    app = types.SimpleNamespace(
        app_state=types.SimpleNamespace(config=config), pdf_extractor=extractor,
        unpaywall_client=None, openalex_cache=None, openalex_client=None, zotero_reader=reader,
        resolve_stage_client=lambda *_args, **_kwargs: object(),
        resolve_stage_provider=lambda *_args, **_kwargs: provider,
    )
    _wire_acquisition(monkeypatch, app, tmp_path, render=render)
    monkeypatch.setattr(deep_review, "get_state", lambda: app)
    monkeypatch.setattr(deep_review, "_load_prestige_context", lambda: ({}, None))
    monkeypatch.setattr(deep_review, "_try_rebuild_render", lambda *_args: None)
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: tmp_path / "deep_reviews.json")
    monkeypatch.setattr(review_detail, "classify_item_key", lambda _key: "feed")
    note_writes = []
    monkeypatch.setattr(
        zotero_svc, "zotero_upsert_digest_note", lambda key, _digest: note_writes.append(key),
    )
    deep_review._set_job("ARTICLE", status="running", started_at="test", completed=0, progress={})
    context = deep_review._build_ctx(reader=reader)
    context["_acquire_missing"] = True
    return config, context, note_writes


def test_article_review_uses_relevance_only_and_current_cache_identity(monkeypatch, tmp_path):
    path = _controlled_pdf(tmp_path)
    extracted, render_calls, classified = [], [], {}
    config, ctx, note_writes = _prepare_review_context(
        monkeypatch, tmp_path,
        render=lambda url, **kwargs: render_calls.append((url, kwargs)) or path,
        extract_text=lambda source: extracted.append(source) or "controlled article text",
    )
    monkeypatch.setattr(
        _map_reduce, "digest_for_strategy",
        lambda *_args, **_kwargs: PaperDigest(
            tldr="Controlled article", read_decision="read", read_why="Relevant",
            read_parts=["Main text"],
        ),
    )
    monkeypatch.setattr(_digest_verification, "verify_digest", lambda *_args, **_kwargs: None)

    class UnexpectedLLM:
        def pydantic_prompt(self, **_kwargs):
            pytest.fail("web articles must bypass scientific paper-type grading")

    def capture_article_type(layers_ctx):
        classified["web_article"] = layers_ctx.web_article
        result = paper_type.detect(
            title=layers_ctx.title, abstract="", headings=[], full_text=layers_ctx.text,
            llm=UnexpectedLLM(), web_article=layers_ctx.web_article,
        )
        return {"quality_band": "neutral", "basis": "non_paper"}, [], result, None, None

    monkeypatch.setattr(_deep_review_layers, "extra_layers", capture_article_type)
    deep_review._review_worker({"item_key": "ARTICLE", "title": "", "pdf_path": ""}, ctx, "")
    entry = deep_review.get_cached_review("ARTICLE")

    assert entry["needs_pdf"] is False and entry["digest"] is not None
    assert extracted == [str(path)]
    assert render_calls == [(
        ARTICLE_URL,
        {"cache_dir": tmp_path, "timeout": config.university_access.fetch_timeout_secs,
         "max_bytes": config.quality_review.max_pdf_bytes},
    )]
    assert entry["acquired_pdf"] == {
        "path": str(path), "source": "web_article", "source_url": ARTICLE_URL,
    }
    assert classified["web_article"] is True
    assert entry["paper_type"]["type"] == paper_type.PaperType.NON_PAPER.value
    assert entry["paper_type"]["source"] == "metadata"
    identity = entry["review_identity"]
    assert identity["source_kind"] == "override" and identity["source_path"] == str(path)
    assert identity["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert note_writes == []


def test_failed_article_render_stays_needs_pdf_with_fetch_diagnostic(monkeypatch, tmp_path):
    _, ctx, note_writes = _prepare_review_context(
        monkeypatch, tmp_path,
        render=lambda *_args, **_kwargs: None,
        extract_text=lambda _path: pytest.fail("no text extraction without a rendered PDF"),
    )

    deep_review._review_worker({"item_key": "ARTICLE", "title": "", "pdf_path": ""}, ctx, "")
    entry = deep_review.get_cached_review("ARTICLE")

    assert entry["needs_pdf"] is True and entry["digest"] is None
    assert entry["acquire_outcome"] == "fetch_failed"
    assert entry["needs_login"] is False
    assert note_writes == []


def test_article_budget_error_stays_a_per_item_failure_without_external_writes(monkeypatch, tmp_path):
    def over_budget(*_args, **_kwargs):
        raise ValueError("article PDF exceeds max_bytes budget")

    _, ctx, note_writes = _prepare_review_context(
        monkeypatch, tmp_path,
        render=over_budget,
        extract_text=lambda _path: pytest.fail("budget failure must stop before review extraction"),
    )
    deep_review._review_worker({"item_key": "ARTICLE", "title": "", "pdf_path": ""}, ctx, "")
    status = deep_review.status("ARTICLE")

    assert status["status"] == "error"
    assert "article PDF exceeds max_bytes budget" in status["error"]
    assert status["diagnostic"]["stage"] == "acquire"
    assert deep_review.get_cached_review("ARTICLE") is None
    assert not (tmp_path / "deep_reviews.json").exists()
    assert note_writes == []


def test_fulltext_caller_does_not_render_or_attach_web_article(monkeypatch, tmp_path):
    from zotero_summarizer.services.library import fulltext

    app = _app()
    render_calls = []
    _wire_acquisition(
        monkeypatch, app, tmp_path,
        render=lambda *args, **kwargs: render_calls.append((args, kwargs)),
    )
    writes = []
    writer = types.SimpleNamespace(
        is_connector_running=lambda: False,
        apply_changes=lambda *args, **kwargs: writes.append((args, kwargs)),
    )
    monkeypatch.setattr(fulltext, "get_zotero_writer_or_raise", lambda: writer)

    result = fulltext.fetch_fulltext_for_items([{
        "item_key": "ARTICLE", "has_pdf": False, "url": ARTICLE_URL, "doi": "",
    }])

    assert result["outcomes"][0]["status"] == "browser_not_attempted"
    assert render_calls == [] and writes == []
