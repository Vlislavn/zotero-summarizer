"""AACR, ESMO and ESMO AI recipes against a stubbed Crossref (no real connection): which rows are
meeting abstracts, their numbers and levels, and that ESMO lists are marked title-only."""
from __future__ import annotations

import httpx

from zotero_summarizer import runtime
from zotero_summarizer.services.conferences import service
from zotero_summarizer.settings import Settings


def _item(doi: str, title: str, abstract: str = "", page: str = "", volume: str = "") -> dict:
    item = {"DOI": doi, "title": [title], "author": [{"given": "A", "family": "B"}], "page": page, "volume": volume}
    if abstract:
        item["abstract"] = f"<jats:p>{abstract}</jats:p>"
    return item


def _crossref(items: list[dict], calls: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"message": {"items": items, "next-cursor": "", "total-results": len(items)}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _edition(tmp_path, block: str, conf_id: str, year: int, items: list[dict]) -> dict:
    settings = Settings.load(project_root=tmp_path)
    runtime.set_context(runtime.AppContext(settings=settings))
    settings.conferences_dir.mkdir(parents=True)
    settings.conferences_config_path.write_text("conferences:\n" + block)
    calls: list[httpx.Request] = []
    assert service.refresh(conf_id, year, http_client=_crossref(items, calls))["ok"]
    assert "offset" not in calls[0].url.params and calls[0].url.params["cursor"] == "*"  # cursor paging only
    return service.edition_view(conf_id, year, page=1, page_size=25, query="")


def test_aacr_keeps_annual_meeting_abstracts_with_text(tmp_path):
    view = _edition(tmp_path, "  - {id: aacr, recipe: aacr, editions: {2026: {from: 2026-04-01, until: 2026-05-31}}}\n",
                    "aacr", 2026, [
        _item("10.1158/1538-7445.am2026-lb333", "Abstract LB333: A late trial", "Results"),
        _item("10.1158/1538-7445.AM2026-5512", "Abstract 5512: A poster", "Methods"),
        _item("10.1158/1538-7445.pancreatic26-a082", "Abstract A082: Another meeting", "x"),
        _item("10.1158/0008-5472.CAN-26-0001", "A regular article", "x")])
    assert view["counts"] == {"total": 2, "late_breaking": 1, "unknown": 1} and view["skipped"] == 2
    assert [r["number"] for r in view["late_breaking"]] == ["lb333"]
    assert {r["title"] for r in view["items"]} == {"A late trial", "A poster"}  # "Abstract NNN:" prefix is not shown
    assert view["summary"]["abstract_available"] and all(r["has_abstract"] for r in view["items"])


def test_esmo_keeps_numbered_supplement_titles_and_is_title_only(tmp_path):
    view = _edition(tmp_path, "  - {id: esmo, recipe: esmo, editions: {2025: {from: 2025-09-01, until: 2025-09-30}}}\n",
                    "esmo", 2025, [
        _item("10.1016/j.annonc.2025.08.442", "5P A poster title", page="S209"),
        _item("10.1016/j.annonc.2025.08.1590", "LBA12 A late-breaking trial", page="S1"),
        _item("10.1016/j.annonc.2025.08.1", "2613O An oral", page="S4"),
        _item("10.1016/j.annonc.2025.08.2", "7P This abstract has been removed because it was not presented at the ESMO Congress", page="S8"),
        _item("10.1016/j.annonc.2025.08.3", "72-2P Another meeting", page="S9"),
        _item("10.1016/j.annonc.2025.01.4", "9P Not a supplement page", page="1234"),
        _item("10.1016/j.annonc.2025.01.5", "Guidance for clinicians", page="12")])
    assert view["counts"] == {"total": 3, "late_breaking": 1, "unknown": 2} and view["skipped"] == 4
    assert [r["number"] for r in view["late_breaking"]] == ["LBA12"]
    assert sorted(r["title"] for r in view["items"]) == ["A late-breaking trial", "A poster title", "An oral"]
    assert view["summary"]["abstract_available"] is False and not any(r["has_abstract"] for r in view["items"])


def test_esmo_ai_keeps_only_the_configured_volume(tmp_path):
    view = _edition(tmp_path, "  - {id: esmo-ai, recipe: esmo_ai, editions: {2026: {from: 2026-11-01, until: 2026-11-30, volume: 14}}}\n",
                    "esmo-ai", 2026, [
        _item("10.1016/j.esmorw.2026.100001", "22P An AI poster", volume="14"),
        _item("10.1016/j.esmorw.2026.100002", "1MO A mini-oral", volume="14"),
        _item("10.1016/j.esmorw.2025.100003", "5P Last year's poster", volume="10"),
        _item("10.1016/j.esmorw.2026.100004", "A regular article", volume="14")])
    assert view["counts"]["total"] == 2 and view["skipped"] == 2
    assert view["summary"]["abstract_available"] is False
