from __future__ import annotations

import logging
import re
from typing import Any

from zotero_summarizer.domain import unique_non_empty_strings  # noqa: F401  (re-exported for services)
from zotero_summarizer.integrations.llm import to_text  # noqa: F401  (re-exported for services)
from zotero_summarizer.models import SummarizeRequest
from zotero_summarizer.runtime import get_context
from zotero_summarizer.settings import Settings


LOGGER = logging.getLogger("zotero_summarizer")


def settings() -> Settings:
    return get_context().settings


def state() -> Any:
    return get_context().state


def setup_logging() -> None:
    current_settings = settings()
    level = getattr(logging, current_settings.app_log_level, logging.INFO)
    LOGGER.setLevel(level)
    LOGGER.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    existing_names = {getattr(handler, "name", "") for handler in LOGGER.handlers}
    if "zotero_file" in existing_names:
        return

    current_settings.app_log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(current_settings.app_log_file, encoding="utf-8")
    file_handler.set_name("zotero_file")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)


def clamp(value: float, low: float, high: float) -> float:
    # NaN must never silently pass: `max(low, min(high, NaN))` returns `high`,
    # which would make a NaN score the top-ranked item. Surface it instead so
    # the bad upstream value (e.g. a malformed LLM number) fails loudly.
    if value != value:
        raise ValueError(f"clamp received NaN (low={low}, high={high})")
    return max(low, min(high, value))


def is_app_rss_source(row: dict[str, Any]) -> bool:
    """True when a feed row/item originated from the app-owned RSS reader
    (``source_type``/``source`` == ``app_rss``) rather than Zotero's feedItems."""
    source = str(row.get("source_type") or row.get("source") or "").strip()
    return source == "app_rss"


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_WS_RE = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace to a single trimmed line."""
    return _HTML_WS_RE.sub(" ", _HTML_TAG_RE.sub(" ", html or "")).strip()


def normalized_title_key(title: str | None) -> str:
    """Title-only identity for display dedup: lowercase, keeping Unicode alphanumerics."""
    return "".join(ch for ch in (title or "").lower() if ch.isalnum())


def log_context(prefix: str, message: str, *args: Any) -> None:
    LOGGER.info("%s " + message, prefix, *args)


def build_log_prefix(
    req: SummarizeRequest,
    item_id: str | None = None,
    batch_id: str | None = None,
    index: int | None = None,
    total: int | None = None,
) -> str:
    parts: list[str] = []
    if batch_id:
        parts.append(batch_id)
    if index is not None and total is not None:
        parts.append(f"{index}/{total}")
    if item_id:
        parts.append(f"item={item_id}")
    title = req.title.strip().replace("\n", " ")[:80]
    parts.append(f"title={title!r}")
    return "[" + " | ".join(parts) + "]"
