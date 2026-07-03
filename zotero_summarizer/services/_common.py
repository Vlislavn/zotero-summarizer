from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable

import yaml

from zotero_summarizer.models import GoalsConfig, SummarizeRequest
from zotero_summarizer.models.config import USER_OWNED_KEYS
from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.runtime import get_context
from zotero_summarizer.services.config_overrides import apply_calibration, apply_env_overrides
from zotero_summarizer.settings import Settings


LOGGER = logging.getLogger("zotero_summarizer")


def settings() -> Settings:
    return get_context().settings


def state() -> Any:
    return get_context().state


def _quality_toggle_enabled(env_var: str, attr: str) -> bool:
    env = os.environ.get(env_var)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    app_state = getattr(state(), "app_state", None)
    config = getattr(app_state, "config", None) if app_state is not None else None
    qr = getattr(config, "quality_review", None) if config is not None else None
    return bool(getattr(qr, attr, False))


def band_primary_enabled() -> bool:
    """Order-time deep-review QUALITY mode, shared by BOTH rank consumers (the
    Library queue ``_ranking`` and the Today slate allocator): grade-only (default
    — the shipped, measured behaviour) vs band-primary (highlight ↑ / flag ↓;
    neutral & uncertain → exactly 0.0), a Phase-2-MEASURED arm. Config
    ``quality_review.quality_band_primary``, overridden by ``ZS_QUALITY_BAND_PRIMARY``.
    Optional-feature boundary: no config / no state → the grade-only default."""
    return _quality_toggle_enabled("ZS_QUALITY_BAND_PRIMARY", "quality_band_primary")


def quality_promote_enabled() -> bool:
    """Quality → must_read band promotion toggle (the inverse of the auto_quality
    hide gate). The gate's compressed regressor never reaches the must_read band on
    its own; promotion lifts a high-quality + on-goal + gate-confirmed paper to
    must_read. ON by default (``tools/eval_quality_promote.py`` measured 0 flooding +
    1.00 must/should precision on firewalled user verdicts). Config
    ``quality_review.quality_promote``, overridden by ``ZS_QUALITY_PROMOTE=0``.
    Optional-feature boundary: no config / no state → OFF (safe when uninitialised)."""
    return _quality_toggle_enabled("ZS_QUALITY_PROMOTE", "quality_promote")


def effective_llm_concurrency(provider: ProviderConfig | None, item_count: int) -> int:
    """Worker count for a per-item LLM fan-out, conditional on the provider.

    A **local** provider (loopback ``base_url``) runs **serial** (1) so one big
    on-device model isn't asked to serve concurrent inference and thrash host
    RAM. A **remote** provider uses the configured ``TRIAGE_JOB_CONCURRENCY`` cap,
    never more than the work on hand. ``provider=None`` falls to the remote
    branch — the safe default never silently serialises a real remote run.
    """
    n = max(1, int(item_count))
    if provider is not None and provider.is_local:
        return 1
    return max(1, min(settings().triage_job_concurrency, n))


def deep_review_fleet_concurrency(provider: ProviderConfig | None, item_count: int) -> int:
    """Worker count for the MULTI-paper deep-review fan-out (the N "Read next" picks the
    fleet / prewarm review at once).

    A **local** provider stays **serial** (1) — one on-device model can't serve
    concurrent inference without thrashing host RAM. A **remote** provider fans out to
    the work on hand, capped by its own ``max_sub_concurrency`` (the per-provider remote
    concurrency budget) when set, else **all N at once** (an API has no host-RAM
    constraint). Deliberately NOT the global ``TRIAGE_JOB_CONCURRENCY`` — that knob
    serialises LOCAL triage for RAM safety, so it must never throttle a genuinely remote,
    RAM-free batch (the bug: with ``TRIAGE_JOB_CONCURRENCY=1`` a 5-pick remote batch ran
    serially). ``provider=None`` falls to the remote branch (never silently serialises a
    real remote run). Peak concurrent calls is this × ``deep_review_sub_concurrency`` (the
    within-paper sub-calls), both bounded by ``max_sub_concurrency`` for a capped provider."""
    n = max(1, int(item_count))
    if provider is not None and provider.is_local:
        return 1
    cap = getattr(provider, "max_sub_concurrency", None) if provider else None
    return min(n, cap) if cap else n


def deep_review_sub_concurrency(provider: ProviderConfig | None) -> int:
    """Number of LLM SUB-calls (self-consistency rubric samples / per-goal
    summaries) to run in parallel WITHIN one paper's deep review.

    A **local** provider stays serial (1) — one big on-device model can't absorb
    concurrent inference without thrashing host RAM. A **remote** provider uses its
    own ``max_sub_concurrency`` cap, falling back to ``TRIAGE_JOB_CONCURRENCY`` when
    unset. ``provider=None`` falls to the remote branch (never silently serialises a
    real remote run). Shared by the background job and the ``verify-deep-review`` CLI
    so the two never drift."""
    if provider is not None and provider.is_local:
        return 1
    configured = getattr(provider, "max_sub_concurrency", None) if provider else None
    if configured is not None:
        return max(1, int(configured))
    return max(1, settings().triage_job_concurrency)


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
    config = _disable_prestige_when_offline(config)
    return _derive_local_num_ctx(config)


def _disable_prestige_when_offline(config: GoalsConfig) -> GoalsConfig:
    """Air-gap contract: ``ZS_OFFLINE`` forces OpenAlex prestige off so triage and
    scoring never reach the network (prestige is now on by default). Applied AFTER
    the env layer so ``ZS_OFFLINE`` beats an explicit ``ZS_PRESTIGE_ENABLED=1``.
    No-op when prestige is already off or the app is online."""
    if not config.prestige.enabled:
        return config
    if (os.getenv("ZS_OFFLINE") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return config
    data = config.model_dump(mode="python")
    data["prestige"]["enabled"] = False
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
    to goals.yaml. System-owned sections (corpus, prestige, quality_review, gate, …)
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
    write_config_atomic(config_path, payload)


def to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("output_text", "text", "result", "summary"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def extract_json_blob(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError as _:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as _:
            pass

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Could not parse JSON content from LLM output")


def safe_parse_response_json(raw: Any, context: str = "") -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            LOGGER.warning("Invalid response_json payload in %s", context or "unknown context")
    return {}


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
    tmp_path = path.with_name(path.name + ".tmp")
    write(tmp_path)
    os.replace(tmp_path, path)


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


def unique_non_empty_strings(values: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        folded = text.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(text)
    return normalized


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso_z() -> str:
    """UTC timestamp, second precision, ``Z`` suffix (e.g. ``2026-05-23T12:00:00Z``)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` as JSON to ``path`` via tmp + ``replace`` (atomic on POSIX),
    creating parent dirs. A crash/race can never leave a half-written cache file.
    The caller owns the envelope (e.g. ``{"updated_at": now_iso_z(), ...}``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_golden_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read the golden CSV into a list of row dicts. Fail-fast if missing."""
    if not csv_path.exists():
        raise FileNotFoundError(f"golden CSV not found at {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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
