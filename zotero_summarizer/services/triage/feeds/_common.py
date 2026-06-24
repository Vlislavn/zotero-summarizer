"""feeds: shared constants, the tick report dataclass, and low-level helpers.

Leaf of the feeds subpackage — every sibling imports from here; this module
imports none of them.
"""
from __future__ import annotations

import logging
import random
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.services._common import settings as get_settings
from zotero_summarizer.services._common import state as get_state
from zotero_summarizer.storage import feeds as feeds_storage

LOGGER = logging.getLogger("zotero_summarizer.services.triage.feeds")

# 8-char Zotero key alphabet — must match ZoteroWriter._KEY_ALPHABET.
_ZOTERO_KEY_ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"

_DEFAULT_BLACK_SWAN_TAG = "🦢 black-swan"


@dataclass
class TriagedCandidate:
    """A feed item that has gone through abstract-only triage."""

    feed_item: dict[str, Any]
    summary: SummarizeResponse
    composite_score: float
    surprise_score: float
    is_black_swan: bool = False
    planned_zotero_key: str | None = None

    @property
    def key(self) -> str:
        return f"{int(self.feed_item.get('feed_library_id') or 0)}:{int(self.feed_item.get('item_id') or 0)}"


@dataclass
class DaemonTickReport:
    """One daemon tick's accounting."""

    tick_id: str
    fetched: int
    skipped_already_processed: int
    skipped_library_dedup: int
    triaged: int
    fast_rejected: int
    errors: int
    marked_read: int
    outcomes_resolved: int
    daily_selection_ran: bool = False
    daily_materialized: int = 0
    daily_rejected: int = 0
    gate_rejected: int = 0       # Phase 1.13: classifier gate fast-rejects
    skipped_processed_dedup: int = 0  # content dupes (different GUID / re-post)
    fatal_llm_error: bool = False  # an LLM endpoint/auth error that will recur
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tick_id": self.tick_id,
            "fetched": self.fetched,
            "skipped_already_processed": self.skipped_already_processed,
            "skipped_processed_dedup": self.skipped_processed_dedup,
            "skipped_library_dedup": self.skipped_library_dedup,
            "triaged": self.triaged,
            "fast_rejected": self.fast_rejected,
            "gate_rejected": self.gate_rejected,
            "errors": self.errors,
            "marked_read": self.marked_read,
            "outcomes_resolved": self.outcomes_resolved,
            "daily_selection_ran": self.daily_selection_ran,
            "daily_materialized": self.daily_materialized,
            "daily_rejected": self.daily_rejected,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


def _generate_zotero_key(seen: set[str]) -> str:
    for _ in range(64):
        candidate = "".join(random.choice(_ZOTERO_KEY_ALPHABET) for _ in range(8))
        if candidate not in seen:
            seen.add(candidate)
            return candidate
    raise RuntimeError("Failed to generate unique Zotero key after 64 attempts")


def _since_iso(default_days: int, override_days: int | None) -> str:
    days = override_days if override_days is not None else default_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def _infer_item_type(feed_item: dict[str, Any]) -> str:
    url = (feed_item.get("url") or "").lower()
    if "arxiv.org" in url:
        return "preprint"
    if "biorxiv" in url or "medrxiv" in url or "chemrxiv" in url:
        return "preprint"
    feed_name = (feed_item.get("feed_name") or "").lower()
    if "arxiv" in feed_name or "preprint" in feed_name:
        return "preprint"
    return "journalArticle"


_HTML_TAG_RE = re.compile(r"<[^>]+>")
# A publisher RSS "abstract" that is only a publication notice, e.g. Nature's
# "Nature, Published online: 17 June 2026; doi:10.1038/s41586-026-10764-5".
_PUB_NOTICE_RE = re.compile(r"published online[:;].*?doi:\s*\S+", re.IGNORECASE | re.DOTALL)


def _has_usable_abstract(item: dict[str, Any], *, min_chars: int = 120) -> bool:
    """True when the feed item carries real abstract prose for the gate to score.

    Prestige-journal RSS (Nature/Science/Cell/NEJM/Annals) ships only a
    publication notice + the title repeated — non-empty, but with no content
    for the gate's abstract-derived features (``abstract_log_len``,
    ``semantic_match_specter2``), which then sink the paper to ``dont_read``.
    Strip HTML, the notice, and the title; if fewer than ``min_chars`` of prose
    remain, the abstract is not usable and the item is a rescue candidate.

    ponytail: a length heuristic, not a per-publisher parser. Tighten with a
    notice allow-list only if a genuinely short real abstract gets flagged.
    """
    text = _HTML_TAG_RE.sub(" ", item.get("abstract") or "")
    text = _PUB_NOTICE_RE.sub(" ", text)
    title = (item.get("title") or "").strip()
    if title:
        text = text.replace(title, " ")
    return len(" ".join(text.split())) >= min_chars


def _safe_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _dim_value(summary: SummarizeResponse, name: str) -> float:
    dims = summary.triage_dimensions
    if dims is None:
        return 0.0
    if hasattr(dims, name):
        try:
            return float(getattr(dims, name) or 0)
        except (TypeError, ValueError):
            return 0.0
    if isinstance(dims, dict):
        try:
            return float(dims.get(name) or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


_FATAL_LLM_ERROR_SIGNALS = (
    "401",
    "403",
    "invalid_api_key",
    "incorrect api key",
    "authentication",
    "permission",
    "quota",
    "insufficient_quota",
    "rate_limit_exceeded",
    "connection refused",
    "connection error",
    "cannot connect",
)


def _is_fatal_llm_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(signal in msg for signal in _FATAL_LLM_ERROR_SIGNALS)


@contextmanager
def _triage_conn() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    path = settings.triage_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error as _:
            pass
        feeds_storage.init_feeds_schema(conn)
        yield conn
    finally:
        conn.close()


def _load_config() -> dict[str, Any]:
    config = get_state().app_state.config
    raw = getattr(config, "raw", {}) or {}
    feeds_cfg = _safe_dict(getattr(config, "feeds", None)) or _safe_dict(raw.get("feeds"))
    selection_cfg = _safe_dict(getattr(config, "selection", None)) or _safe_dict(raw.get("selection"))
    surprise_cfg = _safe_dict(getattr(config, "surprise", None)) or _safe_dict(raw.get("surprise"))
    return {
        "feeds": feeds_cfg,
        "selection": selection_cfg,
        "surprise": surprise_cfg,
    }


def _parse_year(date_str: Any) -> int | None:
    if not date_str:
        return None
    s = str(date_str).strip()
    if len(s) >= 4 and s[:4].isdigit():
        try:
            return int(s[:4])
        except ValueError:
            return None
    return None


def _triage_result_from_summary(summary: SummarizeResponse):
    """Reconstruct a TriageResult from a SummarizeResponse for re-scoring."""
    from zotero_summarizer.models import TriageResult

    return TriageResult(
        score=int(summary.relevance_score),
        reading_priority=summary.reading_priority,
        tags=list(summary.tags),
        confidence=float(summary.triage_confidence),
        rationale=summary.triage_rationale or "(re-scored after prestige lookup)",
        dimensions=summary.triage_dimensions,
    )


def list_feed_groups(reader: ZoteroReader | None = None) -> list[dict[str, Any]]:
    """Convenience pass-through for the CLI `feeds list` subcommand."""
    reader = reader or ZoteroReader(get_settings().zotero_data_dir)
    return reader.get_feed_groups()


def preview_feed(
    feed_library_id: int,
    *,
    since_days: int = 7,
    limit: int = 50,
    reader: ZoteroReader | None = None,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    """Peek at recent feed items for one feed (CLI `feeds preview`)."""
    reader = reader or ZoteroReader(get_settings().zotero_data_dir)
    since = _since_iso(since_days, None)
    return reader.get_feed_items(
        feed_library_id=feed_library_id,
        since=since,
        limit=limit,
        unread_only=unread_only,
    )
