"""REQ-S36 / С1 / С3: a conference download stays under ``data/conferences/`` and reaches the
network only when refresh is called (Crossref is stubbed; no real connection)."""
from __future__ import annotations

import httpx

from zotero_summarizer import runtime
from zotero_summarizer.services.conferences import service
from zotero_summarizer.settings import Settings

MARKER = "ZS-LEAK-MARKER-7f3a9c"


def _item(doi: str, title: str, abstract: str) -> dict:
    return {"DOI": doi, "title": [title], "abstract": f"<jats:p>{abstract}</jats:p>", "author": [{"given": "A", "family": "B"}]}


def _crossref(calls: list[httpx.Request]):
    pages = {"*": ([_item("10.1200/jco.2026.44.17_suppl.LBA1", "Late trial", f"Results {MARKER}"),
                    _item("10.1200/jco.2026.44.16.1234", "Regular JCO article", "not an abstract")], "c2"),
             "c2": ([_item("10.1200/jco.2026.44.17_suppl.8114", "Ordinary abstract", "text")], "c3")}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        items, nxt = pages.get(request.url.params["cursor"], ([], ""))
        return httpx.Response(200, json={"message": {"items": items, "next-cursor": nxt, "total-results": 1500}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_abstract_text_stays_in_conferences_dir_and_network_needs_refresh(tmp_path):
    settings = Settings.load(project_root=tmp_path)
    runtime.set_context(runtime.AppContext(settings=settings))
    settings.conferences_dir.mkdir(parents=True)
    settings.conferences_config_path.write_text(
        "conferences:\n  - id: asco\n    recipe: asco\n    editions:\n      2026: {from: 2026-05-20, until: 2026-06-30}\n")
    calls: list[httpx.Request] = []

    assert service.preflight("asco", 2026)["requests"] == 9  # 42 days x 210 records / 1 000 per page
    assert service.overview()["conferences"][0]["editions"][0]["count"] == 0
    assert calls == []  # reading config and preflight never touch the network

    result = service.refresh("asco", 2026, http_client=_crossref(calls))
    assert result["ok"] and len(calls) == 2
    view = service.edition_view("asco", 2026, page=1, page_size=25, query="")
    assert view["counts"] == {"total": 2, "late_breaking": 1, "unknown": 1} and view["skipped"] == 1
    assert MARKER in service.abstract_of("asco", 2026, "10.1200/jco.2026.44.17_suppl.LBA1")

    holders = {p for p in tmp_path.rglob("*") if p.is_file() and MARKER.encode() in p.read_bytes()}
    assert holders == {settings.conferences_dir / "asco" / "2026.json"}
    assert not settings.corpus_db_path.exists() and not settings.triage_db_path.exists()
