"""Download one edition's abstracts from Crossref (journal-supplement DOIs in a date window).

Facts only: no level is decided here (``catalogue.classify`` does that at read time).
``download`` never raises: a failure comes back with the records that already arrived.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import httpx

from zotero_summarizer.integrations.crossref import CrossrefListError, list_crossref_works
from zotero_summarizer.services.conferences.catalogue import Recipe, abstract_number
from zotero_summarizer.services.conferences.config import Edition

HOST = "api.crossref.org"
PAGE_SIZE = 1000
PAGE_CAP = 40  # ceiling on requests per download, whatever the window
_SELECT = ("DOI", "title", "author", "abstract", "published", "page", "volume")
_FAILURES = {
    "rate_limited": "Crossref asked to wait",
    "http_error": "Crossref answered with an error",
    "transport_error": f"Could not reach {HOST}",
    "parse_error": "Crossref sent a response this app cannot read",
}


@dataclass
class Download:
    records: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0  # rows in the window that are not meeting abstracts (other articles, withdrawn abstracts)
    pages: int = 0
    total: int = 0  # rows Crossref reports for the window
    failure: str = ""  # "" = complete
    retry_after: float | None = None


def estimate_requests(recipe: Recipe, edition: Edition) -> int:
    days = (edition.end - edition.start).days + 1
    return min(PAGE_CAP, max(1, math.ceil(days * recipe.records_per_day / PAGE_SIZE)))


def download(recipe: Recipe, edition: Edition, *, mailto: str | None = None,
             http_client: httpx.Client | None = None) -> Download:
    filters = f"issn:{recipe.issn},from-pub-date:{edition.start.isoformat()},until-pub-date:{edition.end.isoformat()}"
    out, found, cursor = Download(), {}, "*"
    while out.pages < PAGE_CAP:
        try:
            page = list_crossref_works(filters=filters, select=_SELECT, cursor=cursor, rows=PAGE_SIZE,
                                       mailto=mailto, http_client=http_client)
        except CrossrefListError as exc:
            out.failure, out.retry_after = _FAILURES[exc.reason], exc.retry_after
            break
        out.pages, out.total = out.pages + 1, page.total
        for hit in page.hits:
            number = abstract_number(recipe, hit)
            if number is None or (edition.volume and hit.volume != edition.volume):
                out.skipped += 1
            else:
                found[hit.doi.lower()] = {"title": hit.title, "authors": hit.authors, "abstract": hit.abstract,
                                          "doi": hit.doi, "number": number}
        cursor = page.next_cursor
        if not page.seen or not cursor or out.pages * PAGE_SIZE >= page.total:
            break
    else:
        out.failure = f"Reached the {PAGE_CAP}-request limit"
    out.records = list(found.values())
    return out
