"""On-demand verified facts for a paper (Todo #36).

For a paper the collector gathers, by REUSING existing machinery, the facts the
owner decides on: full text (library acquisition chain), the paper's own live code
repo, what is published (prompts / evaluation protocol / judge-vs-human validation
with numbers / agent contracts / data), the reusable-method checklist, and who is
behind it (header affiliations + OpenAlex h-index). No new DB table, no migration,
no LLM, no UI — results are cached as JSON under a Settings data path, keyed by the
paper's STABLE identity (arXiv/DOI), with ``source_sha``, ``extractor_version`` and
per-fact ``fetched_at``/``ttl``.

Keying by arXiv/DOI identity is what makes facts alias-robust: a feed-key and the
Zotero-key for the same paper collapse to one entry. :func:`resolve_ref` fills a
bare key from the feed row through the existing ``feed_key`` alias resolver.

Compute on demand only — Today's top-K and on paper open. GitHub is probed through
the keyless HTML path (``integrations.github``) with liveness cached 7 d; artifacts
30 d; h-index 90 d. Everything fails soft.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zotero_summarizer.domain import normalize_doi
from zotero_summarizer.integrations._zotero_read_common import _arxiv_id_from_url_or_doi
from zotero_summarizer.services._common import LOGGER, settings
from zotero_summarizer.services.card._authors import author_facts
from zotero_summarizer.services.card._code import code_facts
from zotero_summarizer.services.card._fulltext import get_fulltext
from zotero_summarizer.services.card._published import method_checklist, published_facts

EXTRACTOR_VERSION = "card-facts-2"

_DAY = 86400
TTL_LIVENESS = 7 * _DAY      # code repo reachability
TTL_ARTIFACTS = 30 * _DAY    # text source, published chips, method checklist
TTL_HINDEX = 90 * _DAY       # author h-index


@dataclass(frozen=True, slots=True)
class PaperRef:
    """What the collector needs to fetch + identify a paper. ``key`` is the caller's
    handle (feed/Zotero key); ``stable_key`` is the content identity used for caching."""

    key: str
    title: str = ""
    arxiv_id: str = ""
    doi: str = ""
    url: str = ""
    local_pdf: str = ""

    @property
    def stable_key(self) -> str:
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        norm = normalize_doi(self.doi)
        if norm:
            return f"doi:{norm}"
        return f"key:{self.key}"


def _cache_path(stable_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stable_key)[:120]
    return settings().card_facts_dir / f"{safe}.json"


def _load_fresh(path: Path) -> dict[str, Any] | None:
    """Return the cached facts only if the version matches and the shortest TTL
    (code liveness) has not expired — so the cheap re-read never touches the network
    or the PDF."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("extractor_version") != EXTRACTOR_VERSION:
        return None
    code = data.get("code") or {}
    age = int(time.time()) - int(code.get("fetched_at") or 0)
    if age > int(code.get("ttl") or TTL_LIVENESS):
        return None
    return data


def _write(path: Path, facts: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(facts, ensure_ascii=False, indent=1))
    except OSError as exc:
        LOGGER.info("card facts: cache write failed for %s: %s", path, exc)


def collect_facts(
    ref: PaperRef, *, abstract: str = "", offline: bool | None = None, force: bool = False,
) -> dict[str, Any]:
    """Collect (or re-read) the cached facts for ``ref``. The second call for a live
    paper returns from cache without any network or PDF work."""
    path = _cache_path(ref.stable_key)
    if not force:
        cached = _load_fresh(path)
        if cached is not None:
            return cached

    full = get_fulltext(ref, abstract=abstract)
    now = int(time.time())
    facts: dict[str, Any] = {
        "stable_key": ref.stable_key,
        "extractor_version": EXTRACTOR_VERSION,
        "source_sha": full.source_sha,
        "paper": {"key": ref.key, "title": ref.title, "arxiv_id": ref.arxiv_id, "doi": ref.doi},
        "fulltext": {
            "source": full.source, "chars": full.chars, "has_pdf": bool(full.pdf_path),
            "fetched_at": now, "ttl": TTL_ARTIFACTS,
        },
        "code": {**code_facts(full, ref.title, offline=offline), "fetched_at": now, "ttl": TTL_LIVENESS},
        "published": {**published_facts(full.text), "fetched_at": now, "ttl": TTL_ARTIFACTS},
        "method_checklist": {**method_checklist(full.text), "fetched_at": now, "ttl": TTL_ARTIFACTS},
        "authors": {
            **author_facts(full.text, doi=ref.doi, arxiv_id=ref.arxiv_id, title=ref.title),
            "fetched_at": now, "ttl": TTL_HINDEX,
        },
    }
    _write(path, facts)
    return facts


def _feed_row(key: str) -> dict[str, Any] | None:
    """Resolve a feed/legacy key to its processed-feed row via the existing
    ``feed_key`` alias resolver. Fail-soft (no row / no DB → None)."""
    try:
        from zotero_summarizer.storage import feeds_lookup, repositories
        conn = sqlite3.connect(str(repositories._resolve_db_path()))
        conn.row_factory = sqlite3.Row
        try:
            row = feeds_lookup.get_processed_feed_item_by_stable_key(conn, key)
        finally:
            conn.close()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort
        LOGGER.info("card facts: feed-key resolution failed for %s: %s", key, exc)
        return None


def resolve_ref(
    key: str, *, title: str = "", url: str = "", doi: str = "", arxiv_id: str = "", local_pdf: str = "",
) -> PaperRef:
    """Build a :class:`PaperRef`, filling missing identity/fetch fields from the feed
    row (alias-resolved) and from a bare identity key. Explicit arguments always win."""
    k = str(key or "").strip()
    if not (title and (url or doi or arxiv_id)):
        row = _feed_row(k)
        if row:
            title = title or str(row.get("title") or "")
            doi = doi or str(row.get("doi") or "")
            arxiv_id = arxiv_id or str(row.get("arxiv_id") or "")
            url = url or str(row.get("guid") or "")
    # A bare identity key (``arxiv:<id>`` / ``doi:<id>``) carries the identity itself;
    # fill it so the acquisition chain has a fetchable target with no feed row.
    if not arxiv_id:
        arxiv_id = _arxiv_id_from_url_or_doi(k, "")
    if not doi and k.lower().startswith("doi:"):
        doi = k[4:].strip()
    if arxiv_id and not url:
        url = f"https://arxiv.org/abs/{arxiv_id}"
    return PaperRef(key=k, title=title, arxiv_id=arxiv_id, doi=doi, url=url, local_pdf=local_pdf)


__all__ = [
    "EXTRACTOR_VERSION", "PaperRef", "TTL_ARTIFACTS", "TTL_HINDEX", "TTL_LIVENESS",
    "collect_facts", "resolve_ref",
]
