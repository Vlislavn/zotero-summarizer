"""Method-card sidecar: ``paper_render_dir/_method_card/<key>-<source sha>.json``.

Outside ``deep_reviews.json`` on purpose: a card never changes ``review_revision`` or the review
identity, and a re-review does not erase it — the stamp (source sha + prompt hash + model) is
compared afresh on every read."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import settings


def make_stamp(source_sha: str, prompt_sha: str, model: str) -> str:
    return f"{source_sha}:{prompt_sha}:{model}"


def _directory() -> Path:
    return settings().paper_render_dir / "_method_card"


def _prefix(item_key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._]+", "_", item_key)[:80]


def _path(item_key: str, source_sha: str) -> Path:
    return _directory() / f"{_prefix(item_key)}-{re.sub(r'[^0-9a-f]', '', source_sha)[:16]}.json"


def read(item_key: str, source_sha: str, stamp: str) -> dict[str, Any] | None:
    try:
        stored = json.loads(_path(item_key, source_sha).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(stored, dict) and stored.get("stamp") == stamp and stored.get("item_key") == item_key
    return stored if ok else None


def write(item_key: str, source_sha: str, card: dict[str, Any]) -> None:
    """Atomic write; cards of the same paper's older PDF/prompt/model are dropped."""
    path = _path(item_key, source_sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**card, "item_key": item_key}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    for old in path.parent.glob(f"{_prefix(item_key)}-*.json"):
        if old != path:
            old.unlink(missing_ok=True)
