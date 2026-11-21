"""Is the fetched source text a paper at all? (bot-wall / interstitial / paywall stub guard)

A Cloudflare «Performing security verification» page, an «Access Denied» error or a
paywall stub is not the article; a scientific grade, writing-friction label or concern
list derived from it is a fabricated verdict about a paper nobody read. Two deterministic
detectors, no model call:

- ``unreadable_source(text, title)``: new reviews, on the cleaned body BEFORE any LLM stage.
- ``unreadable_review(entry)``: stored reviews, at projection time. A stored review keeps no
  source text, so it reads the model's own wording about the text (narrow patterns, and only
  when the retained body length is absent or short).

Both return a short reason (the matched phrase) or ``None``. A short body alone never counts.
"""
from __future__ import annotations

import re
from typing import Any

# Interstitial / error-page / paywall-stub phrases (lower case, whitespace-normalised).
_PHRASES = (
    "verify you are human", "verifies you are not a bot", "performing security verification",
    "just a moment", "enable javascript and cookies", "checking your browser", "access denied",
    "you don't have permission to access", "automated script or search engine", "content blocked",
    "performance and security by cloudflare", "cloudflare_error", "ray id:", "are you a robot",
    "captcha", "unusual traffic from your", "purchase this article", "buy this article",
    "sign in to read", "get full access to this article", "subscribe to read",
)
_SHORT = 1_500          # a page this short + one phrase is a stub
_DENSE_MAX = 6_000      # a longer page needs >= 3 distinct phrases
_OVERLAP_VETO = 0.6     # share of title words found in the text that proves it is the paper
_OVERLAP_MIN_LEN = 800  # ... once the text is long enough to be more than a stub
_WORD = re.compile(r"[a-z0-9]{4,}")

# The model's own wording about an unreadable source (stored reviews). Narrow on purpose:
# a bare «interstitial» is lung disease, a bare «Cloudflare» may be the topic.
_WORDING = re.compile(
    r"bot[- ]?(?:verification|wall|check)|security[- ]verification|"
    r"(?:verification|challenge|cloudflare|bot)[^.]{0,30}interstitial|interstitial (?:page|notice)|"
    r"cloudflare[^.]{0,40}(?:verification|challenge|notice|block)|just a moment|"
    r"(?:access denied|captcha|paywall)[^.]{0,20}(?:page|notice|stub)|"
    r"(?:text|content|page|body)[^.]{0,30}(?:inaccessible|not accessible)|no paper content", re.I)
_ABOUT_SOURCE = re.compile(r"suppl|provid|retriev|fetch|source|text", re.I)


def unreadable_source(text: str, title: str = "") -> str | None:
    """Reason the cleaned ``text`` is a bot-wall/error/paywall page, else ``None``."""
    flat = " ".join(text.lower().split())
    hits = [p for p in _PHRASES if p in flat]
    if not hits or len(flat) >= _DENSE_MAX or (len(flat) >= _SHORT and len(hits) < 3):
        return None
    words = set(_WORD.findall(title.lower()))
    if len(flat) >= _OVERLAP_MIN_LEN and words and sum(w in flat for w in words) / len(words) >= _OVERLAP_VETO:
        return None
    return f"bot-wall or error page, not the paper (matched {hits[0]!r}; {len(flat)} chars)"


def _model_words(entry: dict[str, Any]) -> list[str]:
    digest = entry.get("model_digest") or entry.get("digest") or {}
    fields = [digest.get(k) for k in ("verdict", "read_why", "tldr", "key_weakness")]
    fields += list(digest.get("writing_reasons") or [])
    fields += list((entry.get("quality") or {}).get("red_flags") or [])
    return [str(f) for f in fields if f]


def unreadable_review(entry: dict[str, Any]) -> str | None:
    """Reason a STORED review rests on an unreadable source, else ``None``."""
    if entry.get("source_unreadable"):
        return str(entry["source_unreadable"])
    body = (entry.get("text_window") or {}).get("body")
    if isinstance(body, int) and body >= _SHORT:
        return None  # a real body was read; the model's words are about something else
    for words in _model_words(entry):
        found = _WORDING.search(words)
        if found and _ABOUT_SOURCE.search(words):
            return f"stored review says the source was not the paper (matched {found.group(0)!r})"
    return None


def withheld_digest(digest: dict[str, Any], reason: str) -> dict[str, Any]:
    """The digest view without scientific verdict fields: no grade, writing friction or
    reasons, no read decision; the read note says the source was not readable (retry)."""
    return {**digest, "grade": "", "writing_friction": None, "writing_reasons": [], "read_decision": "",
            "read_why": f"Source text not readable ({reason}); retry the review once the full text is available."}
