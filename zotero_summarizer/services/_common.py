from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from tempfile import NamedTemporaryFile
from typing import Any, Callable

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


def atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    """Write via a temp file + ``os.replace`` so a crash/race can never leave a
    half-written or truncated file at ``path`` (POSIX rename is atomic). Used for
    irreplaceable artifacts like the golden CSV and the model joblib.
    """
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        write(tmp_path)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def connect_sqlite_ro(db_path: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open a SQLite DB read-only with a ``Row`` factory.

    Pre-checks existence so a missing file raises a clear ``FileNotFoundError``
    instead of an opaque ``sqlite3.OperationalError`` from URI mode.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"database not found at {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn


def is_app_rss_source(row: dict[str, Any]) -> bool:
    """True when a feed row/item originated from the app-owned RSS reader
    (``source_type``/``source`` == ``app_rss``) rather than Zotero's feedItems."""
    source = str(row.get("source_type") or row.get("source") or "").strip()
    return source == "app_rss"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso_z() -> str:
    """UTC timestamp, second precision, ``Z`` suffix (e.g. ``2026-05-23T12:00:00Z``)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json_or_empty(path: Path) -> dict[str, Any]:
    """Read a small JSON state file; a MISSING file → ``{}`` (legitimate absence —
    first run, partially-migrated cache). A file that EXISTS but is unreadable or
    unparseable RAISES — corrupt state is a real signal, never papered over."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: dict[str, Any], *, indent: int | None = None) -> None:
    """Publish complete JSON via ``atomic_write``, creating parent directories.
    The caller owns the envelope (e.g. ``{"updated_at": now_iso_z(), ...}``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, lambda tmp: tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8"))


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_WS_RE = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace to a single trimmed line."""
    return _HTML_WS_RE.sub(" ", _HTML_TAG_RE.sub(" ", html or "")).strip()


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
