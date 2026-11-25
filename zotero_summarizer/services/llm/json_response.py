"""Parse JSON objects out of LLM output (fenced, prose-wrapped) and stored ``response_json`` cells."""
from __future__ import annotations

import json
import re
from typing import Any

from zotero_summarizer.services._common import LOGGER


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
