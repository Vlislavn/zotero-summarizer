"""Direct HTTP PDF fetcher with size/timeout caps and a content-hash cache.

Downloads are streamed; we abort once ``max_bytes`` is exceeded so a malicious
host can't fill the disk. Each successful fetch is saved under the cache dir
keyed by SHA-256 of the bytes; subsequent fetches of the same URL hit the disk
cache. The first 4 bytes are checked against ``%PDF`` so we never feed an
HTML error page into the PDF extractor.

`resolve_pdf_url` produces a URL given paper identifiers; it prefers arXiv
direct PDFs, then Unpaywall, then any URL provided as a fallback.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx

from zotero_summarizer.integrations.app_rss import (
    RssUrlRejected,
    validate_public_response_peer,
    validate_rss_url,
)
from zotero_summarizer.settings import offline_requested


if TYPE_CHECKING:
    from zotero_summarizer.integrations.unpaywall import UnpaywallClient


LOGGER = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF"
_DEFAULT_MAX_BYTES = 50_000_000  # figure-heavy clinical/Nature PDFs run >20 MB
_DEFAULT_TIMEOUT_SECS = 30.0
_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "zotero-summarizer" / "pdfs"

_ARXIV_ID_RE = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
_MAX_REDIRECTS = 5


def _read_pdf_response(resp: httpx.Response, url: str, max_bytes: int) -> bytes | None:
    if resp.status_code >= 400:
        LOGGER.debug("pdf_fetch: HTTP %d for %s", resp.status_code, url)
        return None
    buf = bytearray()
    for chunk in resp.iter_bytes(chunk_size=64_000):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            LOGGER.debug("pdf_fetch: %s exceeded max_bytes=%d", url, max_bytes)
            return None
    if bytes(buf[: len(_PDF_MAGIC)]) != _PDF_MAGIC:
        LOGGER.debug("pdf_fetch: %s missing %%PDF magic", url)
        return None
    return bytes(buf)


def _download_public_pdf(
    client: httpx.Client,
    url: str,
    max_bytes: int,
    *,
    verify_peer: bool,
) -> bytes | None:
    current = validate_rss_url(url) if verify_peer else url
    for _ in range(_MAX_REDIRECTS + 1):
        with client.stream("GET", current, follow_redirects=False) as resp:
            if verify_peer:
                validate_public_response_peer(resp)
            if 300 <= resp.status_code < 400 and resp.headers.get("location"):
                current = validate_rss_url(
                    urljoin(current, str(resp.headers["location"]))
                )
                continue
            return _read_pdf_response(resp, current, max_bytes)
    LOGGER.debug("pdf_fetch: too many redirects for %s", url)
    return None


def fetch_pdf(
    url: str,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout: float = _DEFAULT_TIMEOUT_SECS,
    cache_dir: Path | None = None,
    http_client: httpx.Client | None = None,
) -> Path | None:
    """Stream a PDF to disk; return the cached path or ``None`` on any failure."""
    if not url:
        return None
    cache_dir = (cache_dir or _DEFAULT_CACHE_DIR).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Deterministic per-URL filename — lets us short-circuit on repeat fetches.
    url_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    final_path = cache_dir / f"{url_key}.pdf"
    if valid_pdf_path(final_path, max_bytes=max_bytes):
        return final_path
    if offline_requested():
        return None
    if http_client is None:
        try:
            validate_rss_url(url)
        except RssUrlRejected as exc:
            LOGGER.debug("pdf_fetch: rejected %s: %s", url, exc)
            return None

    client = http_client or httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        body = _download_public_pdf(
            client,
            url,
            max_bytes,
            verify_peer=http_client is None,
        )
        if body is None:
            return None
        tmp_path = cache_dir / f"{url_key}.tmp"
        tmp_path.write_bytes(body)
        tmp_path.replace(final_path)
        return final_path
    except (httpx.HTTPError, OSError, RssUrlRejected) as exc:
        LOGGER.debug("pdf_fetch: error fetching %s: %s", url, exc)
        return None
    finally:
        if http_client is None:
            client.close()


def valid_pdf_path(path: Path, *, max_bytes: int = _DEFAULT_MAX_BYTES) -> bool:
    """A bounded local file with PDF magic, suitable for cache reuse."""
    try:
        if not 0 < path.stat().st_size <= max_bytes:
            return False
        with path.open("rb") as handle:
            return handle.read(4) == _PDF_MAGIC
    except OSError:
        return False


def resolve_pdf_url(
    *,
    doi: str | None,
    arxiv_id: str | None,
    url: str | None,
    unpaywall: "UnpaywallClient | None" = None,
) -> str | None:
    """Pick the best OA PDF URL for a feed item.

    Order: arXiv → Unpaywall (needs DOI) → raw URL (only if it looks like a PDF).
    Returns ``None`` when no OA source is identifiable.
    """
    if arxiv_id:
        cleaned = arxiv_id.strip().lower().replace("arxiv:", "")
        if cleaned:
            return f"https://arxiv.org/pdf/{cleaned}.pdf"
    # Sometimes the URL itself encodes the arxiv ID without a separate field.
    if url and "arxiv.org" in url.lower():
        m = _ARXIV_ID_RE.search(url)
        if m:
            return f"https://arxiv.org/pdf/{m.group(1)}.pdf"
    if doi and unpaywall is not None:
        oa = unpaywall.find_oa_pdf_url(doi)
        if oa:
            return oa
    if url and url.lower().endswith(".pdf"):
        return url
    return None
