"""Built React to real API/session/SQL; inert provider HTTP and no native writes."""
from functools import partial
import os
from types import SimpleNamespace

import httpx
import pytest

from tests.test_application_browser_live import application_browser
from zotero_summarizer.integrations import arxiv, europepmc, openalex
from zotero_summarizer.services.search import discovery, federate, session
from zotero_summarizer.services.search._models import Candidate, QueryPlan, ResearchSession, SearchIntent
from zotero_summarizer.storage import feeds, repositories
from zotero_summarizer.storage.feed_identity import stable_feed_key_from_parts

pytestmark = pytest.mark.skipif(os.environ.get("ZS_APPLICATION_BROWSER_SMOKE") != "1", reason="explicit built Chromium check")


@pytest.mark.parametrize("width", [390, 1440])
def test_built_find_partial_stale_and_real_ordinary_search(application_browser, monkeypatch, width):
    from patchright.sync_api import expect

    page, client, settings, seen = application_browser
    monkeypatch.setenv("ZS_OFFLINE", "0")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    key = stable_feed_key_from_parts(guid="discovery-browser")
    with feeds.open_triage_conn(settings.triage_db_path) as conn:
        feeds.record_decision(conn, run_id="discovery-browser", decision=feeds.DECISION_USER_APPROVED,
                              feed_item={"feed_library_id": 1, "item_id": 1, "guid": "discovery-browser",
                                         "title": "Discovery browser seed", "doi": "10.1/seed"}, composite_score=4.0)
        conn.commit()
    private = "BROWSER_PRIVATE_CONTEXT"
    repositories.upsert_review_note(settings.triage_db_path, key, private)
    public_requests = []

    def provider(request):
        public_requests.append(str(request.url))
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>')
        if request.url.host == "www.ebi.ac.uk":
            return httpx.Response(200, json={"resultList": {"result": []}})
        assert request.url.host == "api.openalex.org"
        if "search.semantic" in request.url.params:
            return httpx.Response(429, text="Controlled rate limit")
        return httpx.Response(200, json={"results": [{"id": "W1", "doi": "10.1/new",
            "title": "External browser candidate", "abstract_inverted_index": {"Public": [0], "evaluation": [1]}}]})

    for module in (arxiv, europepmc, openalex):
        monkeypatch.setattr(module, "_RATE_LIMITER", SimpleNamespace(acquire=lambda: None))
    monkeypatch.setattr(discovery, "score_query_relevance", lambda query, rows, **kwargs:
                        [setattr(candidate, "query_score", 1.0) for candidate in rows])
    with httpx.Client(transport=httpx.MockTransport(provider), trust_env=False) as http:
        source = openalex.OpenAlexClient(object(), http_client=http)
        monkeypatch.setattr(discovery, "_search_openalex_client", lambda *args: source)
        monkeypatch.setattr(federate, "search_arxiv", partial(arxiv.search_arxiv, http_client=http))
        monkeypatch.setattr(federate, "search_europepmc", partial(europepmc.search_europepmc, http_client=http))
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"http://127.0.0.1/paper/{key}")
        find = page.get_by_role("button", name="Find new relevant", exact=True)
        expect(find).to_be_enabled()
        assert not [row for row in seen if row[0] == "POST" and row[1].endswith("/discover")]
        find.click()
        expect(page.get_by_role("region", name="Discovery status")).to_be_visible()
        expect(page.get_by_label("Retrieval status")).to_contain_text("partial")
        expect(page.get_by_role("article", name="External browser candidate")).to_be_visible()
        expect(page.get_by_role("button", name="Add to library", exact=True)).to_be_enabled()
        assert public_requests and all(private not in url for url in public_requests)
        assert len([row for row in seen if row[0] == "POST" and row[1].endswith("/discover")]) == 1
        refresh = page.get_by_role("button", name="Refresh session status", exact=True).bounding_box()
        review = page.get_by_role("button", name="Review retained candidates", exact=True).bounding_box()
        assert min(refresh["height"], review["height"]) >= 44
        assert max(review["x"] - refresh["x"] - refresh["width"],
                   review["y"] - refresh["y"] - refresh["height"]) >= 8
        page.screenshot(path=str(settings.data_dir / f"discovery-current-{width}.png"), full_page=True, animations="disabled")
        repositories.upsert_review_note(settings.triage_db_path, key, "Edited private context")
        page.get_by_role("button", name="Refresh session status", exact=True).click()
        expect(page.get_by_role("region", name="Discovery status")).to_contain_text("Freshness: stale")
        page.get_by_text("1 retained candidates ▾", exact=True).click()
        expect(page.get_by_role("button", name="Add to library", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Review retained candidates", exact=True)).to_be_disabled()
        expect(page.get_by_text(private, exact=True)).to_have_count(0)
        page.screenshot(path=str(settings.data_dir / f"discovery-stale-{width}.png"), full_page=True, animations="disabled")

    ordinary = ResearchSession(id="ordinary-browser", created_at="2026-10-19T00:00:00Z", raw_query="Ordinary",
                               intent=SearchIntent(raw_query="Ordinary"), plan=QueryPlan(), status="reviewed",
                               candidates=[Candidate(title="Ordinary browser candidate", candidate_id="ordinary")])
    session.save(ordinary)
    assert client.get("/api/search/ordinary-browser").json()["discovery"] == {}
    page.goto("http://127.0.0.1/search?session=ordinary-browser")
    expect(page.get_by_role("article", name="Ordinary browser candidate")).to_be_visible()
    expect(page.get_by_role("combobox", name="Sort by", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Add to library", exact=True)).to_be_enabled()
    expect(page.get_by_role("region", name="Discovery status")).to_have_count(0)
    assert not [row for row in seen if row[0] == "POST" and row[1].endswith(("/review", "/materialize"))]
