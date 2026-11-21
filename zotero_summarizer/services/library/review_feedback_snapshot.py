"""Heavy half of a review-feedback package: the paper text and a copy of the PDF the review read.

``begin`` (in the save request) marks ``snapshot.json`` ``pending`` and says whether to run; ``run`` (a
background thread) writes ``source.txt`` / ``source.pdf`` and the final status. The text is re-extracted
from the PDF with the review's own extractor and compared by hash with what the review recorded, so a
changed PDF or extractor shows as ``no`` / ``unknown``, never as a match. Local files only: no model, no network.
"""
from __future__ import annotations

import hashlib
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import LOGGER, atomic_write, now_iso_z
from zotero_summarizer.services.library import _paper_body, _paper_pdf
from zotero_summarizer.services.library.review_feedback_bundle import dump_json, load_json, locked

STALE_AFTER = timedelta(minutes=10)
PDF_COPY_MAX = 25 * 1024 * 1024


def _spawn(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="review-feedback-snapshot", daemon=True).start()


def _stale(state: dict[str, Any]) -> bool:
    try:
        started = datetime.fromisoformat(str(state.get("started_at")).replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(timezone.utc) - started > STALE_AFTER


def mark_interrupted(pkg: Path) -> bool:
    """``pending`` for over 10 minutes (the process stopped mid-snapshot) → ``interrupted``."""
    with locked(pkg / ".feedback.lock"):
        state = load_json(pkg / "snapshot.json")
        if isinstance(state, dict) and state.get("status") == "pending" and _stale(state):
            dump_json(pkg / "snapshot.json", {**state, "status": "interrupted", "finished_at": now_iso_z()})
            return True
    return False


def begin(pkg: Path) -> bool:
    """Record ``pending`` and return True when the caller should run the snapshot now.

    False when it is complete or another run is fresh; an interrupted, partial or failed one is retried."""
    mark_interrupted(pkg)
    with locked(pkg / ".feedback.lock"):
        state = load_json(pkg / "snapshot.json")
        state = state if isinstance(state, dict) else {}
        if state.get("status") in ("complete", "pending"):
            return False
        dump_json(pkg / "snapshot.json", {"status": "pending", "started_at": now_iso_z(),
                                          "previous": state.get("status") or ""})
        return True


def read(pkg: Path) -> dict[str, Any]:
    state = load_json(pkg / "snapshot.json")
    return state if isinstance(state, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _match(actual: str, expected: str) -> str:
    return "unknown" if not (actual and expected) else "yes" if actual == expected else "no"


def _collect(pkg: Path, source: dict[str, str], expected_text_sha: str, extractor: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"source_txt": "none", "source_pdf": "none", "pdf_matches_review": "unknown",
                           "text_matches_review": "unknown", "reasons": []}
    if not source.get("path") or source.get("kind") == "none":
        return out
    try:
        path = _paper_pdf.allowed_path(source["path"])
    except APIError as exc:
        return {**out, "source_txt": "unavailable", "source_pdf": exc.error, "reasons": [exc.error]}
    if not path.is_file():
        return {**out, "source_txt": "unavailable", "source_pdf": "missing", "reasons": ["pdf_missing"]}
    pdf_sha = _sha256_file(path)
    out |= {"pdf_sha256": pdf_sha, "pdf_bytes": path.stat().st_size,
            "pdf_matches_review": _match(pdf_sha, source.get("sha256", ""))}
    if out["pdf_bytes"] > PDF_COPY_MAX:
        out["source_pdf"] = "too_large"
    else:
        tmp = pkg / "source.pdf.tmp"
        shutil.copyfile(path, tmp)
        tmp.replace(pkg / "source.pdf")
        out["source_pdf"] = "copied"
    if extractor is None:
        return {**out, "source_txt": "unavailable", "reasons": ["no_pdf_extractor"]}
    text, _stats = _paper_body.extract_body(extractor, path)
    atomic_write(pkg / "source.txt", lambda tmp: tmp.write_text(text, encoding="utf-8"))
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return out | {"source_txt": "ok", "text_sha256": text_sha, "text_characters": len(text),
                  "text_matches_review": _match(text_sha, expected_text_sha)}


def run(pkg: Path, source: dict[str, str], expected_text_sha: str, extractor: Any) -> None:
    """Write the heavy files and the final ``snapshot.json`` (complete / partial / failed). One run per package."""
    with locked(pkg / ".snapshot.lock", wait=False) as owner:
        if not owner:
            return
        started = (read(pkg) or {}).get("started_at") or now_iso_z()
        try:
            result = _collect(pkg, source, expected_text_sha, extractor)
            done = result["source_txt"] in ("ok", "none") and result["source_pdf"] in ("copied", "too_large", "none")
            result["status"] = "complete" if done else "partial"
        except Exception as exc:  # noqa: BLE001 — a failed snapshot must never lose or fail the feedback
            LOGGER.exception("review feedback snapshot failed: %s", pkg)
            result = {"status": "failed", "reasons": [f"{type(exc).__name__}: {exc}"[:300]]}
        dump_json(pkg / "snapshot.json", {**result, "started_at": started, "finished_at": now_iso_z()})


def launch(pkg: Path, raw: dict[str, Any], extractor: Any) -> None:
    """Start the snapshot in a thread when ``begin`` says it is due."""
    if not begin(pkg):
        return
    identity = raw.get("review_identity") or {}
    source = {"kind": str(identity.get("source_kind") or ""), "path": str(identity.get("source_path") or ""),
              "sha256": str(identity.get("source_sha256") or "")}
    expected = str(((raw.get("verification_audit") or {}).get("source") or {}).get("original_text_sha256") or "")
    _spawn(lambda: run(pkg, source, expected, extractor))
