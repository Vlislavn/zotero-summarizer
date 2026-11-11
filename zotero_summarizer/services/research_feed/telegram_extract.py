"""Faithful Telegram gist: code picks the comments, ONE local-model call writes a line per comment,
code attaches the ids, and a local NLI gate drops lines the cited comments do not entail (Todo #174; measured in
``data/perf-20261107/telegram/faith/phase3``).

Selection (no model): substantive comments, scored by how many replies they got and by relevance to the thread (bge-m3
centrality), diversified (MMR), at most ``K``. The model never writes an id: ``k: line`` rows map to the k-th picked comment.
(A generated «Спор:» line was dropped: ≤ 0.80 supported at every gate threshold on the 30 labelled threads.) Gate: P(entail) of «parent + cited comment» → line must reach ``GATE_TAU``.
A line whose comment answers another picked comment whose line survived carries ``reply_to`` (that comment's stored id: pure reply structure).
A DETECTED disagreement (``telegram_dispute``, at most one per thread) is shown as the two sides' own lines (or short verbatim quotes), never a new sentence.
"""
from __future__ import annotations

import logging
import re

from zotero_summarizer.services.library._prompt_security import UNTRUSTED_INPUT_RULE, untrusted_input
from zotero_summarizer.services.research_feed import telegram_dispute

log = logging.getLogger(__name__)
K = 4
MIN_CHARS = 60        # greetings, one-liners and promo never get this far (Thread keeps only substantive comments)
GATE_TAU = 0.5        # fixed on the dev calibration of phase 2
COMMENT_CHARS, PARENT_CHARS, POST_CHARS, QUOTE_CHARS = 1000, 400, 500, 100
MMR_LAMBDA, ENGAGEMENT_SHARE = 0.6, 0.5
_ASK = ("Для КАЖДОГО из {n} комментариев напиши ОДНУ короткую строку (до 15 слов, по-русски) вида «1: текст», «2: текст» и т.д. (число — номер комментария): "
        "что утверждает его автор. Только то, что сказано в самом комментарии: не добавляй ничего из поста или из родительского комментария, "
        "они даны только для понимания; не смешивай разные комментарии. Если автор иронизирует — передай мысль, которую он имел в виду, "
        "а не буквальную похвалу или согласие. Без вводных вроде «автор считает». Если в комментарии нет ни мысли, ни факта — «3: НЕТ». "
        "Больше ничего не пиши.")
_ROW = re.compile(r"^\W*(?:k\s*:\s*)?(\d+)\s*[:.)]\s*(.+)$")
_NO = re.compile(r"^\W*(?:нет|none)(?:[\s\W]|\[c\d+\])*$", re.IGNORECASE)
_CITE = re.compile(r"\s*\[(?:c\d+(?:\s*[,;]\s*c?\d+)*)\]")
_embedder = []


def _embed(texts: list[str]):
    """bge-m3 on CPU (lazy, process-wide); None when it cannot load → selection falls back to engagement only."""
    if not _embedder:
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer("BAAI/bge-m3", device="cpu")
            model.max_seq_length = 128  # selection needs the gist of a comment only
            _embedder.append(model)
        except Exception as error:
            log.warning("telegram gist: relevance embedder unavailable, selecting by replies only: %s", error)
            _embedder.append(None)
    model = _embedder[0]
    return None if model is None else model.encode(texts, normalize_embeddings=True, batch_size=16)


def _plain(comment, limit: int) -> str:
    return " ".join(comment.text.split())[:limit]


def select(thread) -> list[str]:
    """Short ids (message order) of up to K comments: substantive, replied-to, central, diverse."""
    labels = [label for label in thread.comment if len(_plain(thread.comment[label], 10**6)) >= MIN_CHARS]
    if not labels:
        return []
    replies = {label: 0 for label in thread.comment}
    for label in thread.comment:  # every ancestor counts its descendants
        parent = thread.parent.get(label)
        while parent:
            replies[parent] += 1
            parent = thread.parent.get(parent)
    engagement = [replies[label] / max(max(replies[x] for x in labels), 1) for label in labels]
    vectors = _embed([_plain(thread.comment[label], 400) for label in labels])
    if vectors is None:
        score, similarity = engagement, [[0.0] * len(labels)] * len(labels)
    else:
        similarity = vectors @ vectors.T
        centrality = (similarity.sum(1) - 1) / max(len(labels) - 1, 1)
        spread = max(centrality.max() - centrality.min(), 1e-6)
        score = [ENGAGEMENT_SHARE * e + (1 - ENGAGEMENT_SHARE) * (c - centrality.min()) / spread for e, c in zip(engagement, centrality)]
    chosen: list[int] = []
    while len(chosen) < min(K, len(labels)):
        rest = [i for i in range(len(labels)) if i not in chosen]
        chosen.append(max(rest, key=lambda i: score[i] - MMR_LAMBDA * max((similarity[i][j] for j in chosen), default=0)))
    return sorted((labels[i] for i in chosen), key=lambda label: int(label[1:]))


def _prompt(head: str, thread, picked: list[str]) -> str:
    parts = [UNTRUSTED_INPUT_RULE, head]
    for number, label in enumerate(picked, 1):
        parent = thread.parent.get(label)
        context = f"Ответ на: {untrusted_input(_plain(thread.comment[parent], PARENT_CHARS))}\n" if parent in thread.comment else ""
        parts.append(f"Комментарий {number}:\n{context}Текст {number}: {untrusted_input(_plain(thread.comment[label], COMMENT_CHARS))}")
    parts.append(_ASK.format(n=len(picked)))
    return "\n".join(parts)


def parse(answer: str, picked: list[str]) -> dict[str, str]:
    """{short id: line} from the model's ``k: line`` rows; anything unmapped is dropped."""
    lines: dict[str, str] = {}
    for row in answer.splitlines():
        if (match := _ROW.match(row)) and 1 <= int(match.group(1)) <= len(picked):
            text = _CITE.sub("", match.group(2)).strip()
            if text and not _NO.match(text):
                lines.setdefault(picked[int(match.group(1)) - 1], text)
    return lines


def _premise(thread, labels: list[str]) -> str:
    rows: list[str] = []
    for label in labels:
        parent = thread.parent.get(label)
        if parent in thread.comment and (text := "(родитель) " + _plain(thread.comment[parent], PARENT_CHARS)) not in rows:
            rows.append(text)
        rows.append(_plain(thread.comment[label], 1200))
    return "\n".join(rows)


def _side(thread, label: str, lines: dict[str, str]) -> str:
    """A disagreeing comment as the reader sees it: its verified line, else a short verbatim quote."""
    if label in lines:
        return lines[label]
    quote = _plain(thread.comment[label], 10**6)
    return "«" + (quote if len(quote) <= QUOTE_CHARS else quote[:QUOTE_CHARS].rsplit(" ", 1)[0] + "…") + "»"


def extract(thread, head: str, model, entail=None, noul=None) -> list[dict]:
    """[{text, cites (stored ids)[, reply_to][, kind='disagreement']}]: one line per picked comment that the gate keeps, then at
    most one detected disagreement.

    ``entail(premise, hypothesis) -> float | None`` is the NLI gate; without it (or when it cannot load) lines stay unchecked.
    ``noul(state, questions) -> {id: P(yes)} | None`` is the disagreement detector's engine; without it no disagreement is shown."""
    picked = select(thread)
    if not picked:
        return []
    lines = parse(model(_prompt(head, thread, picked)), picked)
    kept: dict[str, str] = {}
    for label in picked:
        if label not in lines:
            continue
        score = entail(_premise(thread, [label]), lines[label]) if entail else None
        if score is not None and score < GATE_TAU:
            log.info("telegram gist: gate dropped a line (p=%.2f): %.60s", score, lines[label])
            continue
        kept[label] = lines[label]
    points = [{"text": text, "cites": [thread.stored(label)],
               **({"reply_to": thread.stored(thread.parent[label])} if thread.parent.get(label) in kept else {})}
              for label, text in kept.items()]
    if pair := telegram_dispute.find(thread, picked, noul):
        points.append({"kind": "disagreement", "text": " ↔ ".join(_side(thread, label, kept) for label in pair),
                       "cites": [thread.stored(label) for label in pair]})
    return points


__all__ = ["extract", "select", "parse"]
