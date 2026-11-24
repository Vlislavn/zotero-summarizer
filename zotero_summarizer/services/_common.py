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

import yaml

from zotero_summarizer.domain import unique_non_empty_strings  # noqa: F401  (re-exported for services)
from zotero_summarizer.integrations.llm import to_text  # noqa: F401  (re-exported for services)
from zotero_summarizer.models import GoalsConfig, SummarizeRequest
from zotero_summarizer.models.config import USER_KNOBS, USER_OWNED_KEYS
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.config_overrides import apply_calibration, apply_env_overrides
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


def _expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env_placeholders(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env_placeholders(item) for key, item in value.items()}
    return value


# chars-per-token for typical English+paper prose (the standard ~4 assumption; scientific
# text runs slightly denser but 4 is the conservative planning factor).
_CHARS_PER_TOKEN = 4
# Slack on (prompt_tokens + max_tokens) so the KV cache has headroom for the response + any
# thinking phase without a hard truncation at the edge. 1.25 = 25% headroom.
_NUM_CTX_HEADROOM = 1.25


def derive_num_ctx(*, max_text_chars: int, max_tokens: int, headroom: float = _NUM_CTX_HEADROOM) -> int:
    """Context window (tokens) a LOCAL provider needs so a deep-review prompt FITS.

    ``num_ctx = ceil((max_text_chars/chars_per_token + max_tokens) * headroom)`` — large
    enough for the biggest prompt the stage sends (text budget + structured output + thinking
    headroom), small enough not to over-allocate KV-cache RAM on a memory-constrained Mac
    (ollama allocates KV for the FULL window). ollama's own default (~2–4k) silently truncates
    a 60k-char prompt; this sizes it to fit. Pure (testable); re-exported for calibration."""
    if max_text_chars <= 0 or max_tokens <= 0:
        raise ValueError("max_text_chars and max_tokens must be positive")
    prompt_tokens = max_text_chars / _CHARS_PER_TOKEN
    return int((prompt_tokens + max_tokens) * headroom)


def read_config(config_path: Path, calibration_path: Path | None = None) -> GoalsConfig:
    """Load goals.yaml → validate → apply per-user calibration → apply ``ZS_*`` env.
    Precedence: code default < goals.yaml < ``calibration.json`` < ZS_* env. Pass
    ``calibration_path`` (``settings.calibration_path``) to enable the calibration layer;
    omit it (eval/CLI tools that want the raw config) to skip it. See
    ``services/config_overrides.py``."""
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    config = GoalsConfig.model_validate(_expand_env_placeholders(raw))
    if calibration_path is not None:
        config = apply_calibration(config, calibration_path)
    config = apply_env_overrides(config)
    config = _disable_section_when_offline(config, "prestige")
    config = _disable_section_when_offline(config, "openreview")
    config = _disable_section_when_offline(config, "full_text_refine")
    return _derive_local_num_ctx(config)


def _is_offline() -> bool:
    from zotero_summarizer.settings import offline_requested

    return offline_requested()


def _disable_section_when_offline(config: GoalsConfig, section: str) -> GoalsConfig:
    """Air-gap contract: ``ZS_OFFLINE`` forces a network-dependent enrichment section
    off so triage and scoring never reach the network.
    Applied AFTER the env layer so ``ZS_OFFLINE`` beats an explicit ``ZS_*_ENABLED=1``.
    No-op when the section is already off or the app is online."""
    sec = getattr(config, section)
    if not sec.enabled or not _is_offline():
        return config
    data = config.model_dump(mode="python")
    data[section]["enabled"] = False
    return GoalsConfig.model_validate(data)


def _derive_local_num_ctx(config: GoalsConfig) -> GoalsConfig:
    """Auto-size the context window for LOCAL providers that didn't set one explicitly.

    A local ollama provider's own ``num_ctx`` default (~2–4k) silently truncates a long
    deep-review prompt; derive it to fit (``max_text_chars/4 + max_tokens``, +headroom —
    see ``derive_num_ctx``). REMOTE providers keep ``num_ctx=None`` (they size
    their own window; extra_body.num_ctx is a safe no-op there). A provider that set
    ``num_ctx`` explicitly is left alone (user/calibration override). No-op when there's no
    ``llm_routing`` (the legacy flat-llm path)."""
    routing = config.llm_routing
    if routing is None:
        return config
    qr = config.quality_review
    changed = False
    new_providers = []
    for p in routing.providers:
        if p.is_local and p.num_ctx is None:
            p = p.model_copy(update={"num_ctx": derive_num_ctx(max_text_chars=qr.max_text_chars, max_tokens=p.max_tokens)})
            changed = True
        new_providers.append(p)
    if not changed:
        return config
    return config.model_copy(update={"llm_routing": routing.model_copy(update={"providers": new_providers})})


def write_config_atomic(config_path: Path, payload: dict[str, Any]) -> None:
    tmp_path = config_path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as config_file:
        yaml.safe_dump(payload, config_file, sort_keys=False, allow_unicode=False)
    tmp_path.replace(config_path)


def write_user_config(config_path: Path, config: GoalsConfig) -> None:
    """Persist ONLY the user-owned keys (intent + LLM connection + university access)
    to goals.yaml, plus the few ``USER_KNOBS`` fields when non-default. System-owned sections (corpus, prestige, quality_review, gate, …)
    are NEVER written — they stay validated code defaults, refined by calibration and
    ``ZS_*`` env. ``university_access`` is written only when non-default (off → omitted)
    so an unused section never clutters the file. See ``models.config.USER_OWNED_KEYS``."""
    full = config.model_dump(mode="json")
    lean = config.model_dump(mode="json", exclude_defaults=True)
    payload = {
        key: value
        for key, value in full.items()
        if key in USER_OWNED_KEYS and (key != "university_access" or key in lean)
    }
    for section, names in USER_KNOBS.items():
        knobs = {name: lean[section][name] for name in names if name in lean.get(section, {})}
        if knobs:
            payload[section] = knobs
    write_config_atomic(config_path, payload)


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
