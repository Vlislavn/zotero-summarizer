"""Is this paper already in the Zotero library? One check shared by every app Add."""
from __future__ import annotations

import logging
from typing import Any

from zotero_summarizer.integrations._zotero_read_common import _arxiv_id_from_url_or_doi
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services._common import settings
from zotero_summarizer.storage.feeds import _parse_pub_year

LOGGER = logging.getLogger(__name__)


def existing_library_key(
    feed_payload: dict[str, Any], own_key: str = "", arxiv_id: str = ""
) -> str | None:
    """Key of the library item that already is this paper (DOI → arXiv id → title+year,
    see ``ZoteroReader.find_by_external_id``), else None.

    ``feed_payload`` is the create-item payload (title/doi/url/publication_date);
    ``arxiv_id`` is an id the payload's url/doi do not carry.
    ``own_key`` is this Add's own retained key: an item written by an interrupted
    earlier attempt is not a duplicate. A lookup failure is "not found" — it must never
    block an Add."""
    doi = str(feed_payload.get("doi") or "").strip()
    try:
        found = ZoteroReader(settings().zotero_data_dir).find_by_external_id(
            doi=doi or None,
            arxiv_id=arxiv_id or _arxiv_id_from_url_or_doi(str(feed_payload.get("url") or ""), doi) or None,
            title=str(feed_payload.get("title") or "") or None,
            year=_parse_pub_year(feed_payload.get("publication_date")),
        )
    except Exception as exc:  # noqa: BLE001 — external Zotero-read boundary
        LOGGER.info("existing-item lookup failed for %r; adding as new: %s",
                    str(feed_payload.get("title") or "")[:60], exc)
        return None
    return None if found == own_key else found
