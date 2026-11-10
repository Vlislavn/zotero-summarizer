"""Weekly Telegram topics: what the saved channels discuss now, and whether it is new.

Documents are the posts of the last 7 days (headline + body, plus the gist or the longest
top comments when the thread was read). Embeddings are centred on the two-week mean (SPECTER2
cosines on Russian text sit near 0.93 for every pair, so only the deviation carries topic
signal) and joined with TF-IDF over word stems, grouped by average-linkage clustering, and the groups seen in >= MIN_CHANNELS channels
become topics. ONE local-model call writes the label (in the posts' language), one-line gist and notable
ideas/tools per topic (map = clustering, reduce = the call); no model keeps headline labels.
The groups are formed over this and the previous 7 days, so the trend marker compares the same
group's posts of the two weeks. The JSON cache is the only stored result (no migration). Embedder and model are
injected; the model is a ``prompt -> text`` callable from `telegram_processing.model`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from zotero_summarizer.services._common import now_iso_z, write_json_atomic
from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input
from zotero_summarizer.services.research_feed import telegram_ranking as ranking
from zotero_summarizer.services.research_feed import telegram_relevance as relevance
from zotero_summarizer.storage import research_feed_telegram as store

LOG = logging.getLogger(__name__)

WEEK = timedelta(days=7)
MIN_CHARS = 40
DOC_CHARS = 300
NOTE_CHARS = 160
EMBED_BATCH = 64
DISTANCE = 0.8          # cosine cut (centred vectors) for average-linkage groups
SPLIT_FACTOR = 0.8      # a group above MAX_SHARE of the week is split once with this tighter cut
MAX_SHARE = 0.2
MIN_POSTS = 4
MIN_CHANNELS = 3
MAX_TOPICS = 15
HOT_TOPICS = 3          # most-discussed digested threads that stand as topics even without a group
SHOWN = 7               # posts of a topic shown to the model / kept as examples
SCHEMA = 3
_REF = re.compile(r"\s*\[c\d+\]")
_WORD = re.compile(r"[^\W_]{3,}")


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _headline(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")[:100]


def _notes(conn, since: datetime) -> dict[str, str]:
    """source_id -> the longest substantive comments of a stored thread (the gist when known comes later)."""
    notes = {}
    for (payload,) in conn.execute("SELECT payload_json FROM research_feed_conversations WHERE observed_at >= ?",
                                   (since.isoformat(),)):
        value = json.loads(payload)
        origin = value.get("origin") or {}
        texts = sorted((" ".join(m["text"].split()) for m in value.get("messages", [])
                        if m.get("text") and ranking.weight(m["text"]) >= 1.0), key=len, reverse=True)
        if texts:
            notes[f"telegram:{origin.get('channel_id')}:{origin.get('message_id')}"] = " / ".join(
                t[:NOTE_CHARS] for t in texts[:2])
    return notes


def load_docs(conn, now: datetime, gists: dict[str, str]) -> list[dict]:
    """Posts of the last two weeks (deduplicated, text-bearing), oldest first."""
    since = now - 2 * WEEK
    posts = [row["post"] for row in store.list_posts(conn, limit=1000)
             if not row["post"].skip_reason and len(row["post"].text.strip()) >= MIN_CHARS
             and since < _iso(row["post"].published_at) <= now]
    kept, _ = relevance.dedup(posts)
    notes = _notes(conn, since)
    rank = {source_id: number for number, source_id in enumerate(gists)}  # digest order: most discussed first
    docs = []
    for post in kept:
        note = _REF.sub("", " ".join((gists.get(post.source_id) or "").split())) or notes.get(post.source_id, "")
        docs.append({"id": post.source_id, "hot": rank.get(post.source_id), "channel": post.username or "канал", "at": _iso(post.published_at),
                     "headline": _headline(post.text), "permalink": post.permalink, "note": note[:NOTE_CHARS * 2],
                     "text": " ".join(post.text.split())[:DOC_CHARS]})
    return sorted(docs, key=lambda d: (d["at"], d["id"]))


def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0.0, 1.0, norms)


def _stems(text: str) -> list[str]:
    return [word[:5] for word in _WORD.findall(text.casefold()) if not word.isdigit()]


def _embed(scorer, docs: list[dict]) -> np.ndarray:
    """Centred SPECTER2 vectors joined with TF-IDF over word stems (shared names/terms group posts)."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    texts = [f"{d['text']} {d['note']}".strip() for d in docs]
    dense = np.vstack([np.asarray(scorer(texts[i:i + EMBED_BATCH]), dtype=np.float32)
                       for i in range(0, len(texts), EMBED_BATCH)])
    lexical = TfidfVectorizer(analyzer=_stems, min_df=2, max_df=0.2, sublinear_tf=True).fit_transform(texts)
    return _unit(np.hstack([_unit(dense - dense.mean(axis=0)), _unit(lexical.toarray().astype(np.float32))]))


def _groups(vectors: np.ndarray, indexes: list[int], distance: float) -> list[list[int]]:
    from sklearn.cluster import AgglomerativeClustering

    if len(indexes) < 2:
        return [indexes]
    labels = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                     distance_threshold=distance).fit_predict(vectors[indexes])
    found: dict[int, list[int]] = {}
    for index, label in zip(indexes, labels):
        found.setdefault(int(label), []).append(index)
    return list(found.values())


def build_topics(week: list[dict], base: list[dict], scorer) -> list[dict]:
    """Groups over both weeks that hold several of this week's posts from several channels."""
    if len(week) < MIN_POSTS:
        return []
    vectors = _embed(scorer, [*week, *base])
    groups = []
    for group in _groups(vectors, list(range(len(vectors))), DISTANCE):
        groups.extend(_groups(vectors, group, DISTANCE * SPLIT_FACTOR) if len(group) > MAX_SHARE * len(vectors) else [group])
    topics = []
    for group in groups:
        mine = [i for i in group if i < len(week)]
        channels = [week[i]["channel"] for i in mine]
        if len(mine) >= MIN_POSTS and len(set(channels)) >= MIN_CHANNELS:
            centroid = _unit(vectors[mine].mean(axis=0, keepdims=True))[0]
            topics.append({"members": sorted(mine, key=lambda i: -float(vectors[i] @ centroid)),
                           "baseline_posts": len(group) - len(mine), "posts": len(mine),
                           "channels": sorted(set(channels), key=lambda c: (-channels.count(c), c))})
    topics.sort(key=lambda t: (-len(t["channels"]), -t["posts"]))
    for topic in topics:
        topic["trend"] = _trend(topic["posts"], topic["baseline_posts"])
    kept = topics[:MAX_TOPICS - HOT_TOPICS]
    covered = {i for topic in kept for i in topic["members"]}
    hot = sorted((i for i, doc in enumerate(week) if doc["hot"] is not None and i not in covered),
                 key=lambda i: week[i]["hot"])
    kept += topics[len(kept):MAX_TOPICS - min(HOT_TOPICS, len(hot))]
    kept += [{"members": [i], "baseline_posts": 0, "posts": 1, "channels": [week[i]["channel"]], "trend": "hot"}
             for i in hot[:HOT_TOPICS]]
    for number, topic in enumerate(kept, start=1):
        topic["id"] = number
    return kept


def _one_per_channel(members: list[int], week: list[dict]) -> list[int]:
    """Up to 3 member posts, each from a different channel, closest to the topic's centre first."""
    first = {}
    for i in members:
        first.setdefault(week[i]["channel"], i)
    return list(first.values())[:3]


def _trend(posts: int, before: int) -> str:  # a single hot thread is marked 'hot' in build_topics
    return "new" if before <= 1 else "growing" if posts >= 1.5 * before else "steady"


def _label_prompt(topics: list[dict], week: list[dict]) -> str:
    rows = []
    for topic in topics:
        lines = []
        room = NOTE_CHARS * 2 if len(topic["members"]) == 1 else NOTE_CHARS  # a lone thread is its gist
        for i in topic["members"][:SHOWN]:
            doc = week[i]
            lines.append(f"@{doc['channel']}: {doc['headline']}" + (f" ↳ {doc['note'][:room]}" if doc["note"] else ""))
        rows.append(f"Группа {topic['id']}:\n" + "\n".join(lines))
    return "\n".join([
        UNTRUSTED_INPUT_RULE,
        "Ниже группы постов из Telegram-каналов за неделю; посты в группе близки по смыслу. Для каждой группы "
        "назови ТЕМУ (о чём вообще говорят каналы, а не кто с кем спорит) и коротко перескажи суть. "
        "Ответь ровно одной строкой на группу: «<номер> | <тема, 2-4 слова> | <суть одной фразой до 12 слов, сразу "
        "факт или вывод, без вводных «обсуждают», «спорят», «авторы»> | "
        "<1-2 заметные идеи, инструмента или факта через ';'>». Если у постов группы нет общей темы, "
        "напиши «<номер> | -»; так же отвечай про мемы, анонсы, рекламу, личные истории и дайджесты без темы. "
        "Пример строки: «7 | выбор языка программирования | Rust быстрее в разработке, Go проще в поддержке | "
        "Rust; профилирование». Пиши на языке постов (русские посты — по-русски) и только по переданным текстам.",
        untrusted_input("\n\n".join(rows))])


def _apply_labels(topics: list[dict], answer: str) -> None:
    by_id = {t["id"]: t for t in topics}
    for line in answer.splitlines():
        parts = [part.strip(" «»\"*`") for part in line.split("|")]
        match = re.match(r"\D*(\d+)\D*$", parts[0]) if len(parts) > 1 else None
        topic = by_id.get(int(match.group(1))) if match else None
        if topic is None:
            continue
        if parts[1] in {"-", "—", ""}:
            topic["dropped"] = True
            continue
        rest = [piece.strip() for piece in "; ".join(parts[2:]).split(";") if piece.strip() and piece.strip() != "-"]
        topic.update(label=parts[1][:60], label_source="model", gist=rest[0][:160] if rest else None, ideas=rest[1:3])


def _fingerprint(week: list[dict], base: list[dict]) -> str:
    rows = [f"{d['id']}:{hashlib.sha1(d['note'].encode()).hexdigest()[:6]}" for d in [*week, *base]]
    return hashlib.sha256(f"tp5|{'|'.join(rows)}".encode()).hexdigest()


def weekly_trends(conn, now: datetime, *, scorer=None, model=None, gists: dict[str, str] | None = None,
                  cache_path: Path | None = None) -> dict:
    """Topics for the 7 days ending ``now`` (``gists``: source_id -> thread gist of digested posts).

    ``scorer(texts) -> vectors`` defaults to the shared SPECTER2 path; ``model(prompt) -> text``
    makes exactly one labelling call (None keeps headline labels). An unchanged week is served from ``cache_path``.
    """
    if now.utcoffset() is None:
        raise ValueError("weekly_trends requires an aware datetime")
    docs = load_docs(conn, now, gists or {})
    week = [d for d in docs if d["at"] > now - WEEK]
    base = [d for d in docs if d["at"] <= now - WEEK]
    fingerprint = _fingerprint(week, base)
    if cache_path is not None and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            LOG.warning("telegram trends cache unreadable; recomputing")
            cached = {}
        cached = cached if isinstance(cached, dict) else {}
        if cached.get("schema") == SCHEMA and cached.get("fingerprint") == fingerprint and (model is None or cached.get("labelled_by_model")):
            return cached
    scorer = scorer or relevance.specter2_scorer
    topics = build_topics(week, base, scorer) if len(week) >= MIN_POSTS else []
    for topic in topics:
        head = week[topic["members"][0]]
        topic.update(label=head["headline"][:60], label_source="headline", gist=None, ideas=[])
    label_error = None
    if model is not None and topics:
        try:
            _apply_labels(topics, model(_label_prompt(topics, week)))
        except Exception as error:
            label_error = type(error).__name__
    shown = [t for t in topics if not t.get("dropped")]
    result = {"schema": SCHEMA, "fingerprint": fingerprint, "computed_at": now_iso_z(),
              "week_start": (now - WEEK).isoformat(), "week_end": now.isoformat(),
              "posts": {"week": len(week), "baseline": len(base), "channels": len({d["channel"] for d in week})},
              "topics": [{"id": t["id"], "label": t["label"], "gist": t["gist"], "ideas": t["ideas"],
                          "trend": t["trend"], "posts": t["posts"], "baseline_posts": t["baseline_posts"],
                          "channels": t["channels"][:4], "channel_count": len(t["channels"]),
                          "source_ids": [week[i]["id"] for i in t["members"]],
                          "examples": [{"channel": week[i]["channel"], "headline": week[i]["headline"],
                                        "permalink": week[i]["permalink"]} for i in _one_per_channel(t["members"], week)]}
                         for t in shown],
              "labelled_by_model": any(t["label_source"] == "model" for t in shown), "label_error": label_error}
    if cache_path is not None:
        write_json_atomic(cache_path, result)
    return result


__all__ = ["weekly_trends", "load_docs", "build_topics"]
