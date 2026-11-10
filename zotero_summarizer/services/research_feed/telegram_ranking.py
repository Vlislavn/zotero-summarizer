"""Discussion-first ranking of Telegram posts: what people say matters, goal match is a soft boost.

Pure functions, no DB/network. ``goal`` is the relevance gate's cosine mapped to 0..1 above
``tau`` (probe hits are 1.0), so an off-goal but lively post (a game) can still make the digest
while an on-goal post nobody discusses sinks. Messages carry no author identity, so
"substance" is comment count plus total length, not distinct participants.
"""
from __future__ import annotations

import math
import re

GOAL_BOOST = 0.3      # perfect goal match is worth about a dozen comments
MIN_SCORE = 0.15      # a single short reply is not a discussion
FULL_COMMENTS = 200   # substantive-comment count that saturates the activity term (whole threads are read)
FULL_CHARS = 12000    # total substantive comment length that saturates the substance term
PROMO_WEIGHT = 0.1    # a link-only / self-promo / self-intro comment counts a tenth of a substantive one
_URL = re.compile(r"https?://\S+|t\.me/\S+|@\w+", re.IGNORECASE)
_PROMO = re.compile(
    r"(?:мо[йяеё]\w*|наш\w*)\s+(?:канал|телеграм\w*|блог|чат|резюме|портфолио)|\bcv\b"
    r"|подписыва\w+|подпиш\w+|(?:пиши\w*|писать)\s+в\s+(?:лс|личк\w+|директ)|ищу\s+(?:работу|заказ\w*)|\bin\s+my\s+channel\b"
    r"|#\w*(?:ищу|резюме|вакан)\w*|меня\s+зовут|\bведу\b|(?-i:\bЯ\s+[—–-]\s+[А-ЯЁ]\w+\s+[А-ЯЁ]\w+)"
    r"|\bя\s+(?:автор|ведущ\w+|основател\w+|владел\w+)|\bмо[йяеи]\w*\s+(?:магазин|подкаст\w*|услуг\w*|курс\w*|студи\w*|проект\w*)"
    r"|^\W*(?:добрый\s+(?:день|вечер)|здравствуйте)\b"
    r"|в\s+(?:своем|своём|моем|моём)\s+канале|канал\w*\s+(?:завел\w*|завёл\w*)|присоединя\w+|\bрассказываю\s+(?:про|о)\b|t\.me/\w+(?!/\d)",
    re.IGNORECASE)


def weight(text: str) -> float:
    """1 for substantive text (a link with real words still counts), PROMO_WEIGHT for bare links,
    self-promotion ('вот мой канал', CV) and one-word reactions."""
    words = _URL.sub(" ", text).split()
    return PROMO_WEIGHT if len(words) < 3 or _PROMO.search(text) else 1.0


def goal_fit(score: float, tau: float) -> float:
    """Relevance-gate score -> 0..1 (0 at or below ``tau``)."""
    return min(1.0, max(0.0, (score - tau) / (1.0 - tau)))


def _activity(count: int) -> float:
    return min(1.0, math.log1p(count) / math.log1p(FULL_COMMENTS))


def prior(replies_count: int, goal: float) -> float:
    """Pre-fetch ranking from the post's reply counter alone."""
    return _activity(replies_count) + GOAL_BOOST * goal


def score(comments, replies_count: int, goal: float) -> float:
    """Final ranking from the fetched comments (messages with ``.text``) and the post's reply counter."""
    weights = [weight(c.text) for c in comments]
    effective = sum(weights)
    # The counter may exceed what was fetched: scale it by the share of substantive comments seen.
    count = max(effective, replies_count * effective / len(comments)) if comments else replies_count
    chars = sum(len(c.text) * w for c, w in zip(comments, weights))
    return 0.6 * _activity(count) + 0.4 * min(1.0, chars / FULL_CHARS) + GOAL_BOOST * goal


__all__ = ["goal_fit", "prior", "score", "weight", "MIN_SCORE"]
