"""Pure forward/text dedup and goal-cosine relevance gate for the one pipeline.

No DB, no network, no model lifecycle. The embedder/probe are injected so the
caller owns which model actually runs; this module only ranks and selects.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import numpy as np

from zotero_summarizer.models.research_feed import TelegramPost

DEFAULT_TAU = 0.35
_WORD = re.compile(r"\w+", re.UNICODE)


def _ordinal(post: TelegramPost) -> tuple:
    """Earliest-first ordering for collapsing duplicates."""
    return (post.published_at, post.channel_id, post.message_id)


def _normalized_text(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(folded.split())


def dedup(posts: list[TelegramPost]) -> tuple[list[TelegramPost], dict]:
    """Drop exact forwards (fwd_from) and near-duplicate text, keeping the earliest.

    Returns the retained posts (input order preserved) and collapse counts.
    """
    ordered = sorted(posts, key=_ordinal)
    seen_forward: dict[tuple[int, int], TelegramPost] = {}
    seen_text: dict[str, TelegramPost] = {}
    dropped: set[tuple[int, int]] = set()
    forward_dups = text_dups = 0
    for post in ordered:
        if post.fwd_from is not None:
            key = (post.fwd_from[0], post.fwd_from[1])
            if key in seen_forward:
                dropped.add((post.channel_id, post.message_id))
                forward_dups += 1
                continue
            seen_forward[key] = post
        normalized = _normalized_text(post.text)
        if normalized:
            if normalized in seen_text:
                dropped.add((post.channel_id, post.message_id))
                text_dups += 1
                continue
            seen_text[normalized] = post
    kept = [p for p in posts if (p.channel_id, p.message_id) not in dropped]
    info = {"input": len(posts), "kept": len(kept), "collapsed": len(dropped),
            "forward_dups": forward_dups, "text_dups": text_dups}
    return kept, info


@dataclass(frozen=True)
class GateResult:
    """One post's relevance decision with its 'why for you' goal."""

    post: TelegramPost
    kept: bool
    score: float
    matched_goal: str | None
    route: str  # "cosine" | "probe"


def _unit_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0.0, 1.0, norms)


def _is_mostly_non_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(1 for c in letters if "LATIN" in unicodedata.name(c, ""))
    return latin < 0.5 * len(letters)


def keyword_probe(text: str, goals: list[str]) -> bool:
    """Cheap offline yes/no fallback: any goal word token present in the post."""
    tokens = {m.group(0).casefold() for m in _WORD.finditer(text)}
    for goal in goals:
        words = {m.group(0).casefold() for m in _WORD.finditer(goal) if len(m.group(0)) > 2}
        if words & tokens:
            return True
    return False


def _hit_goal(text: str, goals: list[str]) -> str:
    """The first goal the keyword probe matches (an injected probe falls back to the first goal)."""
    return next((goal for goal in goals if keyword_probe(text, [goal])), goals[0])


def gate(posts: list[TelegramPost], goals: list[str], *, scorer, min_keep: int,
         top_n: int, tau: float = DEFAULT_TAU, probe=None) -> list[GateResult]:
    """Goal-cosine gate: keep top_n per channel above tau, always >= min_keep.

    ``scorer(texts) -> np.ndarray`` embeds English/abstract text (SPECTER2). For a
    mostly-non-Latin post, ``probe(text, goals) -> bool`` decides instead when it is
    supplied (multilingual model or a yes/no probe); otherwise cosine still applies.
    """
    if not posts:
        return []
    if not goals:
        raise ValueError("relevance gate requires at least one owner goal")
    scored: list[GateResult] = []
    cosine_posts = [p for p in posts if not (probe is not None and _is_mostly_non_latin(p.text))]
    cosine_index = {id(p): i for i, p in enumerate(cosine_posts)}
    if cosine_posts:
        goal_vectors = _unit_rows(np.asarray(scorer(goals), dtype=np.float32))
        post_vectors = _unit_rows(np.asarray(scorer([p.text for p in cosine_posts]), dtype=np.float32))
        sims = post_vectors @ goal_vectors.T
    for post in posts:
        if id(post) in cosine_index:
            row = sims[cosine_index[id(post)]]
            best = int(np.argmax(row))
            scored.append(GateResult(post, kept=False, score=float(row[best]),
                                     matched_goal=goals[best], route="cosine"))
        else:
            hit = bool(probe(post.text, goals))
            scored.append(GateResult(post, kept=hit, score=1.0 if hit else 0.0,
                                     matched_goal=_hit_goal(post.text, goals) if hit else None, route="probe"))
    return _select(scored, min_keep=min_keep, top_n=top_n, tau=tau)


def _select(scored: list[GateResult], *, min_keep: int, top_n: int, tau: float) -> list[GateResult]:
    by_channel: dict[int, list[GateResult]] = {}
    for result in scored:
        by_channel.setdefault(result.post.channel_id, []).append(result)
    kept_ids: set[int] = set()
    for channel_results in by_channel.values():
        ranked = sorted(channel_results, key=lambda r: r.score, reverse=True)
        for result in ranked[:top_n]:
            if result.score >= tau or result.route == "probe" and result.kept:
                kept_ids.add(id(result))
    if len(kept_ids) < min_keep:
        for result in sorted(scored, key=lambda r: r.score, reverse=True):
            if id(result) in kept_ids:
                continue
            kept_ids.add(id(result))
            if len(kept_ids) >= min_keep:
                break
    return [GateResult(r.post, kept=id(r) in kept_ids, score=r.score,
                       matched_goal=r.matched_goal if id(r) in kept_ids else r.matched_goal,
                       route=r.route) for r in scored]


def specter2_scorer(texts: list[str]) -> np.ndarray:
    """English/abstract SPECTER2 embedder wrapper (loads the shared model).

    The one pipeline passes this for English channels; never call it from the
    offline harness (it would load the heavy encoder)."""
    from zotero_summarizer.services.model import classifier_embed

    tok, mdl, torch = classifier_embed._load_specter2()
    device = classifier_embed._MODEL_CACHE["device"]
    return classifier_embed._encode_chunk(tok, mdl, torch, list(texts), device)


__all__ = ["dedup", "gate", "GateResult", "keyword_probe", "specter2_scorer", "DEFAULT_TAU"]
