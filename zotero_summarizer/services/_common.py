from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any

from fastapi.responses import FileResponse
import yaml

from zotero_summarizer.models import GoalsConfig, SummarizeRequest
from zotero_summarizer.runtime import get_context
from zotero_summarizer.settings import Settings


LOGGER = logging.getLogger("zotero_summarizer")
PACKAGE_WEB_DIR = Path(__file__).resolve().parents[1] / "web"


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


def read_config(config_path: Path) -> GoalsConfig:
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return GoalsConfig.model_validate(_expand_env_placeholders(raw))


def write_config_atomic(config_path: Path, payload: dict[str, Any]) -> None:
    tmp_path = config_path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as config_file:
        yaml.safe_dump(payload, config_file, sort_keys=False, allow_unicode=False)
    tmp_path.replace(config_path)


def web_file(name: str) -> Path:
    # Project-level overrides are kept for local development, but packaged files
    # are the supported source after repo cleanup.
    project_file = settings().project_root / name
    if project_file.exists():
        return project_file
    return PACKAGE_WEB_DIR / name


def html_file_response(path: Path) -> FileResponse:
    response = FileResponse(str(path), media_type="text/html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


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
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
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
    return max(low, min(high, value))


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
