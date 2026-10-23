"""Strict bounded saved-input admission, never Telegram transport or execution."""
from __future__ import annotations

import json
from pathlib import Path

from zotero_summarizer.models.research_feed import TelegramPost


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError(f"nonfinite JSON value: {value}")


def read_posts(path: Path, *, limit: int) -> list[TelegramPost]:
    posts = []
    identities = set()
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if len(posts) >= limit:
                raise ValueError(f"{path}:{line_number}: saved input exceeds source limit {limit}")
            try:
                raw = json.loads(line, object_pairs_hook=_object, parse_constant=_nonfinite)
                post = TelegramPost.model_validate(raw)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: invalid saved Telegram post") from exc
            if post.source_id in identities:
                raise ValueError(f"{path}:{line_number}: duplicate Telegram identity {post.source_id}")
            identities.add(post.source_id)
            posts.append(post)
    return posts


__all__ = ["read_posts"]
