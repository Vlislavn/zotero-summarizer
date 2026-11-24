"""One edition = ``<conferences_dir>/<id>/<year>.json`` (+ ``.json.prev``, + ``.partial.json``).

Writes are atomic. The complete file is replaced only by a complete download, after the
previous one is kept as ``.prev``; an interrupted download leaves the complete file
untouched and parks what arrived in ``.partial.json``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import settings
from zotero_summarizer.services._io import atomic_write, write_json_atomic


def _path(conf_id: str, year: int, suffix: str = ".json") -> Path:
    return settings().conferences_dir / conf_id / f"{year}{suffix}"


def save_complete(conf_id: str, year: int, payload: dict[str, Any]) -> None:
    final = _path(conf_id, year)
    if final.is_file():
        atomic_write(_path(conf_id, year, ".json.prev"), lambda tmp: shutil.copyfile(final, tmp))
    write_json_atomic(final, payload)
    _path(conf_id, year, ".partial.json").unlink(missing_ok=True)


def save_partial(conf_id: str, year: int, payload: dict[str, Any]) -> None:
    write_json_atomic(_path(conf_id, year, ".partial.json"), payload)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_CACHE_MAX = 3  # parsed complete files kept (a 30 MB parse per page request is too slow)
_cache: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}


def load_complete(conf_id: str, year: int) -> dict[str, Any] | None:
    path = _path(conf_id, year)
    try:
        stat = path.stat()
    except OSError:
        return None
    stamp, name = (stat.st_mtime_ns, stat.st_size), str(path)
    if name not in _cache or _cache[name][0] != stamp:
        _cache.pop(name, None)
        while len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[name] = (stamp, _read(path))
    return _cache[name][1]


def partial_summary(conf_id: str, year: int) -> dict[str, Any] | None:
    """``{saved_at, count, message}`` of an interrupted download, without its records."""
    data = _read(_path(conf_id, year, ".partial.json"))
    if data is None:
        return None
    return {"saved_at": data.get("saved_at", ""), "count": len(data.get("records", [])), "message": data.get("message", "")}
