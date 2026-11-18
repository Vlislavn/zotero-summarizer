"""Parse the owner's ``data/conferences/config.yaml`` (path from ``Settings``).

The file is read on every call, so an edit applies without a restart. Each block is
checked on its own: a typo in one yields an error naming its line and the other
conferences still load.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from zotero_summarizer.services.conferences.catalogue import RECIPES

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")  # becomes a directory name: no separators, no dots


@dataclass(frozen=True)
class Edition:
    year: int
    start: date
    end: date
    volume: str = ""  # optional: keep only rows of this journal volume (ESMO AI: one volume per year)


@dataclass(frozen=True)
class Conference:
    id: str
    recipe: str
    editions: tuple[Edition, ...]


@dataclass(frozen=True)
class ConfigError:
    line: int
    message: str


@dataclass(frozen=True)
class ConferenceConfig:
    exists: bool
    conferences: tuple[Conference, ...]
    errors: tuple[ConfigError, ...]


def _day(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _edition(year_key: Any, params: Any) -> Edition:
    if not re.fullmatch(r"(19|20)\d\d", str(year_key)):
        raise ValueError(f"edition year '{year_key}' must be a 4-digit year")
    if not isinstance(params, dict) or "from" not in params or "until" not in params:
        raise ValueError(f"edition {year_key} needs 'from' and 'until' dates")
    try:
        start, end = _day(params["from"]), _day(params["until"])
    except ValueError:
        raise ValueError(f"edition {year_key}: 'from'/'until' must be dates like 2026-05-20") from None
    if end < start:
        raise ValueError(f"edition {year_key}: 'until' is before 'from'")
    volume = str(params.get("volume", "")).strip()
    if volume and not volume.isdigit():
        raise ValueError(f"edition {year_key}: 'volume' must be a number like 14")
    return Edition(int(year_key), start, end, volume)


def _conference(block: Any) -> Conference:
    if not isinstance(block, dict):
        raise ValueError("each conference must be a mapping with id, recipe, editions")
    conf_id, recipe = str(block.get("id", "")), str(block.get("recipe", ""))
    if not _ID_RE.match(conf_id):
        raise ValueError(f"id '{conf_id}' must be lowercase letters, digits and '-'")
    if recipe not in RECIPES:
        raise ValueError(f"unknown recipe '{recipe}' (known: {', '.join(sorted(RECIPES))})")
    editions = block.get("editions")
    if not isinstance(editions, dict) or not editions:
        raise ValueError("'editions' must map a year to its from/until window")
    return Conference(conf_id, recipe, tuple(sorted((_edition(k, v) for k, v in editions.items()), key=lambda e: e.year)))


def _block_lines(text: str) -> list[int]:
    """1-based line of each item under ``conferences:`` (for error messages)."""
    root = yaml.compose(text, Loader=yaml.SafeLoader)
    if isinstance(root, yaml.MappingNode):
        for key, value in root.value:
            if key.value == "conferences" and isinstance(value, yaml.SequenceNode):
                return [item.start_mark.line + 1 for item in value.value]
    return []


def load_config(path: Path) -> ConferenceConfig:
    if not path.is_file():
        return ConferenceConfig(False, (), ())
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text) or {}
        lines = _block_lines(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        return ConferenceConfig(True, (), (ConfigError(mark.line + 1 if mark else 1, "not valid YAML"),))
    blocks = raw.get("conferences") if isinstance(raw, dict) else None
    if not isinstance(blocks, list):
        return ConferenceConfig(True, (), (ConfigError(1, "expected a 'conferences:' list"),))
    found: list[Conference] = []
    errors: list[ConfigError] = []
    for index, block in enumerate(blocks):
        line = lines[index] if index < len(lines) else 1
        try:
            conference = _conference(block)
        except ValueError as exc:
            errors.append(ConfigError(line, str(exc)))
            continue
        if any(c.id == conference.id for c in found):
            errors.append(ConfigError(line, f"id '{conference.id}' is already used above"))
        else:
            found.append(conference)
    return ConferenceConfig(True, tuple(found), tuple(errors))
