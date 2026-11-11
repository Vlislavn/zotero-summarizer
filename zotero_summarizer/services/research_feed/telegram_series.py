"""Series links between stored channel posts: which earlier post a post continues.

A post continues another when it says so structurally (``reply_to_id``, stored for posts
ingested after the capture was added) or points to it with a ``t.me/<channel>/<id>`` link under a
continuation marker («часть 2», «продолжение», «предыдущий пост») within 40 characters of it. Only posts that are stored
(a tracked channel, an earlier message) can be a parent; nothing is guessed from similarity.
"""
from __future__ import annotations

import re

_LINK = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]{4,})/(\d+)")
_MARKER = r"(?:част[ьи]|part)\s*[2-9]\b|продолжени|предыдущ|previous|continued"
# the marker must sit right before/after the link ("Продолжение: t.me/x/5"), not anywhere in the post
_NEAR = re.compile(rf"(?:{_MARKER})\w*\W.{{0,40}}?{_LINK.pattern}|{_LINK.pattern}.{{0,40}}?(?:{_MARKER})", re.IGNORECASE | re.DOTALL)


def _pairs(match) -> list[tuple[str, str]]:
    groups = [g for g in match.groups() if g is not None]
    return [(groups[0], groups[1])] if len(groups) >= 2 else []


def parents(posts) -> dict[str, object]:
    """source_id -> the stored post it continues (the nearest earlier one when it links several)."""
    by_key = {(post.username.casefold(), post.message_id): post for post in posts if post.username}
    by_id = {(post.channel_id, post.message_id): post for post in posts}
    found = {}
    for post in posts:
        candidates = []
        if getattr(post, "reply_to_id", None):
            candidates.append(by_id.get((post.channel_id, post.reply_to_id)))
        links = [(name, number) for match in _NEAR.finditer(post.text) for name, number in _pairs(match)]
        candidates += [by_key.get((name.casefold(), int(number))) for name, number in links]
        earlier = [c for c in candidates if c is not None and c.source_id != post.source_id
                   and (c.published_at, c.message_id) < (post.published_at, post.message_id)]
        if earlier:
            found[post.source_id] = max(earlier, key=lambda c: (c.published_at, c.message_id))
    return found


__all__ = ["parents"]
