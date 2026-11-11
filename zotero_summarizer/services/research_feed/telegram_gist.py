"""Per-post discussion digest: a few faithful lines about the comments that matter, and the comments that matter to the owner.

``gist_points`` delegates to ``telegram_extract`` (code picks up to 4 comments, ONE call writes a line each, ids come
from code, a local NLI gate drops unsupported lines, a Jev pair check may add one detected disagreement). 'Важное' is picked deterministically from ALL comments, wherever they sit in the
thread: a goal-probe hit (a bonus, never required), or concrete facts (two of: a number, named tools, a link with real text; one number
or two tool names is enough in a >= 60-character comment). No model means no gist, but the picks still work.
"""
from __future__ import annotations

import logging
import re

from zotero_summarizer.services.library._prompt_security import untrusted_input
from zotero_summarizer.services.research_feed import telegram_progress as progress
from zotero_summarizer.services.research_feed import telegram_ranking as ranking
from zotero_summarizer.services.research_feed.telegram_extract import extract

_NUMBER = re.compile(r"\d")
_LINK = re.compile(r"https?://")
_NAME = re.compile(r"\b[A-Za-z][\w.+-]{2,}\b")
log = logging.getLogger(__name__)
GIST_VERSION = "g4"   # bump when the gist prompt, selection or gate changes (recorded in the digest)
POST_CHARS = 500   # as in the measured prototype; the lines describe comments, the post is context
PARENT_CHARS = 600
IMPORTANT_MAX = 3
QUOTE_CHARS = 200
FACT_MIN_CHARS = 60   # a single concrete fact (no goal hit) needs a substantial comment
MEDIA_ONLY = "[в посте нет текста — только картинка или файл; тема видна лишь из комментариев]"


class Thread:
    """Substantive comments in thread order under short ids c1..cN, their parents, and the way back to stored ids."""

    def __init__(self, comments):
        root_id = next((c.thread_id for c in comments), None)
        kept = sorted((c for c in comments if ranking.weight(c.text) >= 1.0), key=lambda c: c.message_id)
        self.short = {c.message_id: f"c{n}" for n, c in enumerate(kept, 1)}
        self.comment = {self.short[c.message_id]: c for c in kept}
        self.parent = {label: self.short.get(c.reply_to_id) if c.reply_to_id != root_id else None for label, c in self.comment.items()}

    def stored(self, label: str) -> str:
        """Stored comment id (``c<message_id>``) of a short id."""
        return f"c{self.comment[label].message_id}"


def _head(post, parent) -> str:
    body = post.text.strip()[:POST_CHARS] or MEDIA_ONLY
    head = "Пост: " + untrusted_input(body)
    if parent is not None:
        head = "Предыдущий пост серии (читатели обсуждают продолжение): " + untrusted_input(parent.text.strip()[:PARENT_CHARS]) + "\n" + head
    return head


def gist_points(post, comments, model, parent=None, entail=None, noul=None) -> list[dict]:
    """Points about the thread, each ``{text, cites}`` (stored ids assigned by code, never by the model; see ``telegram_extract``)."""
    thread = Thread(comments)
    head = _head(post, parent)
    log.info("telegram gist %s: %d substantive comments", post.source_id, len(thread.comment))
    return extract(thread, head, model, entail, noul)


def important(comments, goals, probe) -> list[dict]:
    """Up to three comments that hit an owner goal or carry concrete facts, wherever they are in the thread."""
    picks = []
    promo_thread = sum(ranking.weight(c.text) < 1.0 for c in comments) * 2 >= len(comments)  # facts there are self-introductions
    for comment in comments:
        text = " ".join(comment.text.split())
        if ranking.weight(text) < 1.0:
            continue
        goal = next((g for g in goals if probe(text, [g])), None)
        link = bool(_LINK.search(text))
        facts = bool(_NUMBER.search(text)) + (len(set(_NAME.findall(text))) >= 2) + link
        points = 2 * (goal is not None) + facts * (not promo_thread)
        if points >= 2 or points - link >= 1 and len(text) >= FACT_MIN_CHARS:  # a goal is a bonus, not a requirement
            picks.append((points + min(len(text), 600) / 600, comment.message_id, goal, text))
    picks.sort(key=lambda pick: pick[:2], reverse=True)
    return [{"id": f"c{message_id}", "goal": goal,
             "quote": text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rstrip() + "…"}
            for _, message_id, goal, text in picks[:IMPORTANT_MAX]]


def thread_rows(messages: list[dict]) -> list[dict]:
    """Stored comments for the page, in message order: stored id, parent id, full text."""
    return [{"id": f"c{m['message_id']}", "reply_to": f"c{m['reply_to_id']}" if m.get("reply_to_id") else None,
             "text": m["text"].strip()}
            for m in sorted(messages, key=lambda m: m["message_id"]) if m["text"].strip() and not m.get("skip_reason")]


def summaries(shown, comments_by_post, *, model, goals, probe, parents=None, entail=None, noul=None):
    """One entry per shown post with comments: gist (or 'no summary' + top ids) and 'важное для вас'."""
    entries, fallback = [], 0
    for index, (result, _) in enumerate(shown):
        progress.report("summaries", index, len(shown))
        post = result.post
        comments = comments_by_post.get((post.channel_id, post.message_id))
        if not comments:
            continue
        parent = (parents or {}).get(post.source_id)
        entry = {"source_id": post.source_id, "matched_goal": result.matched_goal,
                 "comment_count": len(comments), "important": important(comments, goals, probe),
                 "continues": parent.source_id if parent else None}
        found = [] if model is None else gist_points(post, comments, model, parent, entail, noul)
        text = "\n".join(p["text"] + "".join(f" [{c}]" for c in p["cites"]) for p in found if p.get("kind") != "disagreement")
        if not found:
            entry.update(label="no summary", summary=None, points=[],
                         top_comment_ids=[f"c{c.message_id}" for c in comments[:3]])
            fallback += 1
        else:
            entry.update(label="summary", summary=text, points=found, comment_ids=[f"c{c.message_id}" for c in comments])
        entries.append(entry)
    return entries, fallback


__all__ = ["gist_points", "important", "summaries", "thread_rows", "GIST_VERSION"]
