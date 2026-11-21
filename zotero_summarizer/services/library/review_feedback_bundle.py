"""Review-feedback evidence package: what each part showed, the review, the lock and the writer.

One package per paper and review version under ``Settings.review_feedback_dir``:
``<paper>/<version>/{feedback,review,snapshot,check}.json`` plus ``source.txt`` / ``source.pdf``
(the heavy files come from ``review_feedback_snapshot``). The database row is the source of truth for
``feedback.json``; the package is rewritten from it. No model call, no network, no write outside the
package root. ``render_shown`` is the ONE place that turns a part's data keys into the text the owner saw.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import LOGGER, now_iso_z, settings, write_json_atomic

WHOLE = "whole"
SHOWN_MAX = 2000
_TRUNCATED = "…[truncated]"
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,199}")

# part -> (label, ((dotted data key, label shown before its value), ...)); keys are read from the
# projected review (``get_usable_review``), the same record the page renders.
PARTS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "verdict": ("Verdict", (("digest.read_decision", "Decision"), ("digest.estimated_read_minutes", "Minutes"),
                            ("digest.read_why", "Why"))),
    "top_sentence": ("Top sentence", (("digest.tldr", "Contribution"), ("digest.verdict", "Assessment"))),
    "quality": ("Quality", (("digest.grade", "Grade"), ("quality.coverage_met", "Checks met"),
                            ("quality.coverage_applicable", "Checks applicable"), ("quality.quality_band", "Band"),
                            ("quality.missing_critical", "Missing critical"), ("quality.rubric", "Rubric"),
                            ("quality.evidence", "Evidence"))),
    "red_flags": ("Red flags", (("quality.red_flags", "Red flags"), ("quality.overstatements", "Overstatements"))),
    "metrics": ("Metrics", (("digest.parameters.metrics", "Metrics"),)),
    "strength_weakness": ("Strength / Weakness", (("digest.key_strength", "Strength"),
                                                  ("digest.key_weakness", "Weakness"))),
    "reading_plan": ("Reading plan", (("digest.read_parts", "Read first"), ("digest.skip_parts", "Skip"),
                                      ("digest.original_value", "What the original adds"))),
    "findings": ("Findings and applicability", (("digest.key_findings", "Key findings"),)),
    "assessment": ("Assessment", (("digest.parameters", "Study"), ("digest.impact", "Impact"),
                                  ("digest.industry_impact", "Industry"), ("digest.academy_impact", "Academia"),
                                  ("digest.unknown_unknowns", "Unknowns"), ("digest.controversies", "Controversies"),
                                  ("digest.implementation", "Implementation"))),
    "relevance": ("Relevance", (("goal_summaries", "Goals"),)),
    "methods_limits": ("Methods and limitations", (("digest.methods", "Methods"),
                                                   ("digest.limitations", "Limitations"))),
    "code_link": ("Code link", (("code_link", "Code link"),)),
}
FIELDS = (*PARTS, WHOLE)


def valid_key(item_key: str) -> str:
    """The paper key, or ``review_not_found`` — keys name a folder, so no path syntax gets through."""
    if not _KEY.fullmatch(item_key or "") or ".." in item_key:
        raise APIError("review_not_found", "This paper has no review to give feedback on", status_code=404)
    return item_key


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _lines(value: Any, depth: int) -> list[str]:
    pad = "  " * depth
    if isinstance(value, dict):
        pairs = [(f"{key}:", item) for key, item in value.items() if not _empty(item)]
    elif isinstance(value, list):
        pairs = [("-", item) for item in value if not _empty(item)]
    else:
        return [f"{pad}{value}"]
    out: list[str] = []
    for head, item in pairs:
        if isinstance(item, (dict, list)):
            out += [f"{pad}{head}", *_lines(item, depth + 1)]
        else:
            out.append(f"{pad}{head} {item}")
    return out


def _value(review: dict[str, Any], dotted: str) -> Any:
    node: Any = review
    for name in dotted.split("."):
        node = node.get(name) if isinstance(node, dict) else None
    return node


def render_shown(field: str, review: dict[str, Any]) -> str:
    """Deterministic text of a part's data keys (``label: value`` lines, empty keys skipped), ≤2000 chars.

    The panel on the page and ``feedback.json`` both carry this text; the browser never supplies it."""
    if field not in PARTS:
        return ""
    out: list[str] = []
    for dotted, label in PARTS[field][1]:
        value = _value(review, dotted)
        if _empty(value):
            continue
        if isinstance(value, (dict, list)):
            out += [f"{label}:", *_lines(value, 1)]
        else:
            out.append(f"{label}: {value}")
    text = "\n".join(out)
    return text if len(text) <= SHOWN_MAX else text[:SHOWN_MAX - len(_TRUNCATED)] + _TRUNCATED


def package_dir(item_key: str, reviewed_at: str, generation_sha256: str) -> Path:
    """``<root>/<safe key>-<key hash>/<reviewed_at without colons>-<generation prefix>``."""
    paper = f"{re.sub(r'[^A-Za-z0-9_.-]', '_', item_key)[:40]}-{hashlib.sha256(item_key.encode()).hexdigest()[:8]}"
    stamp = re.sub(r"[^0-9A-Za-z._-]", "", reviewed_at.replace(":", "")) or "unknown"
    return settings().review_feedback_dir / paper / f"{stamp}-{generation_sha256[:8] or 'nogen'}"


def dump_json(path: Path, payload: Any) -> None:
    """Atomic, human-readable JSON (the package is read without the app)."""
    write_json_atomic(path, payload, indent=1)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@contextmanager
def locked(path: Path, *, wait: bool = True) -> Iterator[bool]:
    """``flock`` on ``path`` (created 0600). With ``wait=False`` yields False instead of blocking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def _article(item_key: str, previous: dict[str, Any]) -> dict[str, str]:
    """Title / DOI / URL: kept from an earlier write, else a best-effort library lookup (never fails the save)."""
    kept = previous.get("article")
    if isinstance(kept, dict) and kept.get("title"):
        return kept
    try:
        from zotero_summarizer.services.zotero.zotero import resolve_reader_for_key

        detail = resolve_reader_for_key(item_key).get_item_detail(item_key) or {}
    except Exception:  # noqa: BLE001 — Zotero absent/locked: the package just lacks the title
        LOGGER.warning("review feedback: no article metadata for %s", item_key, exc_info=True)
        detail = {}
    return {name: str(detail.get(name) or "") for name in ("title", "doi", "url")}


def build_feedback(item_key: str, row: dict[str, Any] | None, review: dict[str, Any],
                   previous: dict[str, Any]) -> dict[str, Any]:
    """``feedback.json``: the row's parts (tombstones as ``withdrawn``) next to what identifies the review."""
    stored = (row or {}).get("fields") or {}
    parts: dict[str, Any] = {}
    for field in PARTS:
        if field in stored:
            parts[field] = {**stored[field], **({} if stored[field].get("withdrawn") else
                                                {"keys": [key for key, _ in PARTS[field][1]]})}
    earlier = (previous.get("parts") or {}).get(WHOLE)
    if row and (row["chips"] or row["note"]):
        parts[WHOLE] = {"chips": row["chips"], "note": row["note"], "at": row["updated_at"]}
    elif earlier:  # cleared since the last write: keep the record that it was given
        parts[WHOLE] = earlier if earlier.get("withdrawn") else {"withdrawn": True, "at": now_iso_z()}
    identity, freshness = review.get("review_identity") or {}, (review.get("review_freshness") or {}).get("status")
    return {
        "schema": 1, "item_key": item_key, "article": _article(item_key, previous),
        "reviewed_at": review.get("reviewed_at") or "", "generation_sha256": identity.get("generation_sha256") or "",
        "model": (review.get("provenance") or {}).get("model") or "",
        "source": {"kind": identity.get("source_kind") or "", "path": identity.get("source_path") or "",
                   "sha256": identity.get("source_sha256") or ""},
        "review_freshness": freshness if freshness in ("current", "outdated") else "unknown",
        "saved_at": now_iso_z(), "parts": parts,
    }


def write_package(item_key: str, row: dict[str, Any] | None, review: dict[str, Any], raw: dict[str, Any]) -> Path:
    """Write ``review.json`` (once per version: stored + as shown) and refresh ``feedback.json``.

    Light and synchronous; the caller then asks ``review_feedback_snapshot`` for the heavy files."""
    identity = review.get("review_identity") or {}
    pkg = package_dir(item_key, str(review.get("reviewed_at") or ""), str(identity.get("generation_sha256") or ""))
    with locked(pkg / ".feedback.lock"):
        if not (pkg / "review.json").exists():
            dump_json(pkg / "review.json", {"stored": raw, "shown": review})
        previous = load_json(pkg / "feedback.json")
        dump_json(pkg / "feedback.json", build_feedback(item_key, row, review, previous if isinstance(previous, dict) else {}))
    return pkg
