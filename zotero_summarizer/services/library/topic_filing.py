"""File a newly Added paper into the topic tree (Jev variant A, pilot-proven: data/library-cleanup/pilot/pilot.md).

One Jev ``choice`` over every leaf («Topic / Leaf: definition») + NONE gives scores; the top leaf is the main one, up to two
more at/above ``T_ALSO`` are «also» leaves, and a top score below ``T_ABSTAIN`` (or NONE winning) files nothing. The tree is the
list ``{topic, leaf, definition}`` in ``Settings.topic_tree_path`` (exact live collection names). ``topic_paths`` classifies BEFORE the Add and
returns the leaf collections for the Add's own ``matched_collections`` (atomic, no extra backup); engine off / no tree /
abstain / error → Inbox only, never raises.
"""
from __future__ import annotations

import json
import logging

from zotero_summarizer.integrations import jev
from zotero_summarizer.integrations.zotero_read import ZoteroReader
from zotero_summarizer.services._common import settings, state
from zotero_summarizer.services.llm import jev_engine

LOGGER = logging.getLogger(__name__)
NONE = "NONE"
T_ABSTAIN, T_ALSO, MAX_ALSO = 0.25, 0.20, 2
_TIMEOUT = 10.0  # seconds; a hung engine must not hold an Add (pilot: ~0.2 s per call)
_CHOICE_RULE = ("Choose the topic the paper is MAINLY about. Where domain and technique compete the domain wins "
                "(a medical memory system belongs to Medical AI). A paper that only touches a topic does not belong to it.")
_NONE_RULE = "NONE: the paper is not about any of the options (e.g. physics, astronomy, general work or life articles)."
_missing_logged = False


def load_tree() -> list[dict[str, str]]:
    """``[{topic, leaf, definition}]`` from ``Settings.topic_tree_path``; ``[]`` (INFO once) when absent or unreadable."""
    global _missing_logged
    path = settings().topic_tree_path
    try:
        tree = json.loads(path.read_text(encoding="utf-8"))
        return [{key: str(row.get(key) or "") for key in ("topic", "leaf", "definition")} for row in tree if row.get("leaf")]
    except (OSError, ValueError, AttributeError):
        if not _missing_logged:
            _missing_logged = True
            LOGGER.info("topic filing off: no readable topic tree at %s", path)
        return []


def _leaf_text(row: dict[str, str]) -> str:
    return f"{row['topic']} / {row['leaf']}" + (f": {row['definition']}" if row["definition"] else "")


def leaf_scores(target: jev.Target, title: str, abstract: str, tree: list[dict[str, str]]) -> dict[str, float] | None:
    """{leaf: P} over the tree + NONE from one Jev request; ``None`` when the engine fails (never a class)."""
    criteria = {**{row["leaf"]: _leaf_text(row) for row in tree}, NONE: "not about any of the topics"}
    question = {"c": {"type": "choice", "criteria": criteria,
                      "instructions": f"Which option is `paper` about? {_CHOICE_RULE} {_NONE_RULE}"}}
    answers = jev.ask(target, {"paper": f"Title: {title}\nAbstract: {abstract}"}, question, timeout=_TIMEOUT)
    try:
        return None if answers is None else {name: float(p) for name, p in answers["c"]["probabilities"].items()}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def pick_leaves(scores: dict[str, float] | None) -> list[str]:
    """Main leaf first, then up to ``MAX_ALSO`` leaves ≥ ``T_ALSO``; ``[]`` = abstain (low top score, NONE wins, or no scores)."""
    ranked = sorted(((s, leaf) for leaf, s in (scores or {}).items() if leaf != NONE), reverse=True)
    if not ranked or ranked[0][0] < T_ABSTAIN or (scores or {}).get(NONE, 0.0) >= ranked[0][0]:
        return []
    return [ranked[0][1], *[leaf for s, leaf in ranked[1:1 + MAX_ALSO] if s >= T_ALSO]]


def classify(title: str, abstract: str, target: jev.Target, tree: list[dict[str, str]]) -> list[str]:
    """Leaves for one paper (main first); ``[]`` = Inbox only."""
    return pick_leaves(leaf_scores(target, title, abstract, tree))


def _live_leaves(tree: list[dict[str, str]]) -> set[tuple[str, str]]:
    """{(topic, leaf)} of the tree that exist in Zotero now — an absent collection would fail the whole Add."""
    live = {(top["name"].casefold(), kid["name"].casefold())
            for top in ZoteroReader(settings().zotero_data_dir).get_collections() for kid in top["children"]}
    return {(row["topic"], row["leaf"]) for row in tree if (row["topic"].casefold(), row["leaf"].casefold()) in live}


def topic_paths(title: str, abstract: str) -> list[str]:
    """``'Topic > Leaf'`` collection paths (main first) to add to a NEW item inside its own Add (``matched_collections``).
    Classified BEFORE the write, so filing is atomic with the Add and costs no extra backup. ``[]`` (Inbox only) when
    filing is off, there is no tree/engine, Jev abstains or anything fails — a failure here never blocks the Add."""
    try:
        config = state().app_state.config
        if not config.topic_filing_enabled or not title.strip():
            return []
        tree, target = load_tree(), jev_engine.resolve(config)
        if not tree or target is None:
            return []
        leaves = classify(title, abstract, target, tree)
        topic_of = {row["leaf"]: row["topic"] for row in tree}
        live = _live_leaves(tree) if leaves else set()
        paths = [f"{topic_of[leaf]} > {leaf}" for leaf in leaves if (topic_of[leaf], leaf) in live]
    except Exception as exc:  # noqa: BLE001 — best-effort; Inbox only
        LOGGER.info("topic filing skipped: %s", type(exc).__name__)
        return []
    LOGGER.info("topic filing: %s", paths)
    return paths
