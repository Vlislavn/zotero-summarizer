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

import httpx


if TYPE_CHECKING:
    from zotero_summarizer.integrations.unpaywall import UnpaywallClient


LOGGER = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF"
_DEFAULT_MAX_BYTES = 20_000_000
_DEFAULT_TIMEOUT_SECS = 30.0
_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "zotero-summarizer" / "pdfs"

_ARXIV_ID_RE = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")


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
    if final_path.exists() and final_path.stat().st_size > 0:
        return final_path

    client = http_client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                LOGGER.debug("pdf_fetch: HTTP %d for %s", resp.status_code, url)
                return None
            buf = bytearray()
            for chunk in resp.iter_bytes(chunk_size=64_000):
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    LOGGER.debug("pdf_fetch: %s exceeded max_bytes=%d", url, max_bytes)
                    return None
            if len(buf) < len(_PDF_MAGIC) or not bytes(buf[: len(_PDF_MAGIC)]) == _PDF_MAGIC:
                LOGGER.debug("pdf_fetch: %s missing %%PDF magic", url)
                return None
            tmp_path = cache_dir / f"{url_key}.tmp"
            tmp_path.write_bytes(bytes(buf))
            tmp_path.replace(final_path)
            return final_path
    except (httpx.HTTPError, OSError) as exc:
        LOGGER.debug("pdf_fetch: error fetching %s: %s", url, exc)
        return None
    finally:
        if http_client is None:
            client.close()


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
