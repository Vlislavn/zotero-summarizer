"""Owner feedback on one review: reasons (chips) + a note per part of the review, user-study instrument.

Keyed by paper and review version: the server snapshots ``reviewed_at``, the generation hash and the
model from the stored review, so a client cannot attach feedback to a version that does not exist, and
the text a part showed is computed here (``review_feedback_bundle.render_shown``), never taken from the
browser. The ``whole`` part is the row's own chips/note; every other part lives in ``fields``. Each save
refreshes the evidence package on disk; a package failure never fails the save.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import LOGGER, now_iso_z, settings, state
from zotero_summarizer.services.library import _review_cache, deep_review, review_feedback_bundle as bundle
from zotero_summarizer.services.library import review_feedback_check as check, review_feedback_snapshot as snapshot
from zotero_summarizer.storage import repositories, review_feedback as store

CHIPS = ("methods_not_useful", "wrong_or_unsupported_fact", "flags_noise", "too_long",
         "missing_code_or_data", "wrong_read_depth", "grade_unclear", "good_as_is")
NOTE_MAX = 1000
WHOLE = bundle.WHOLE


def _known(chips: list[str]) -> list[str]:
    unknown = [chip for chip in chips if chip not in CHIPS]
    if unknown:
        raise APIError("review_feedback_chip_unknown", f"Unknown chip {unknown[0]!r}", status_code=422)
    return list(dict.fromkeys(chips))


def _current_review(item_key: str, field: str, reviewed_at: str | None) -> dict[str, Any]:
    """The paper's usable review, or 404 / 409 (the page shows another version) / 422 (unknown part)."""
    if field not in bundle.FIELDS:
        raise APIError("unknown_field", f"Unknown review part {field!r}", status_code=422)
    review = deep_review.get_usable_review(bundle.valid_key(item_key))
    if review is None:
        raise APIError("review_not_found", "This paper has no review to give feedback on", status_code=404)
    if reviewed_at is not None and reviewed_at != str(review.get("reviewed_at") or ""):
        raise APIError("review_version_changed", "This review was regenerated; reload the page",
                       status_code=409)
    return review


def _version(review: dict[str, Any]) -> tuple[str, str, str]:
    identity, provenance = review.get("review_identity") or {}, review.get("provenance") or {}
    return (str(review.get("reviewed_at") or ""), str(identity.get("generation_sha256") or ""),
            str(provenance.get("model") or ""))


def _publish(item_key: str, row: dict[str, Any] | None, review: dict[str, Any]) -> dict[str, str]:
    """Refresh the package and start the heavy snapshot; report where it is. Never raises."""
    try:
        raw = _review_cache._read_all().get(item_key) or {}
        pkg = bundle.write_package(item_key, row, review, raw)
        snapshot.launch(pkg, raw, getattr(state(), "pdf_extractor", None))
        return {"path": _relative(pkg), "snapshot": str(snapshot.read(pkg).get("status") or "")}
    except Exception:  # noqa: BLE001 — the row is saved; the evidence package is best effort
        LOGGER.exception("review feedback package failed for %s", item_key)
        return {"path": "", "snapshot": "failed"}


def _relative(pkg: Path) -> str:
    return str(pkg.relative_to(settings().data_dir))


def _write(item_key: str, review: dict[str, Any], mutate) -> dict[str, Any]:
    with closing(repositories._connect_to(settings().triage_db_path)) as conn, conn:
        return store.update(conn, item_key, _version(review), mutate)


def put_part(item_key: str, field: str, reviewed_at: str | None, chips: list[str], note: str) -> dict[str, Any]:
    """Save or replace one part's reasons and note; needs at least one of them (else use ``delete_part``)."""
    kept, text = _known(chips), note.strip()[:NOTE_MAX]
    if not (kept or text):
        raise APIError("review_feedback_empty", "Pick a reason or write a note, or withdraw this part",
                       status_code=422)
    review = _current_review(item_key, field, reviewed_at)

    def mutate(row: dict[str, Any]) -> None:
        if field == WHOLE:
            row["chips"], row["note"] = kept, text
        else:
            row["fields"][field] = {"chips": kept, "note": text, "shown": bundle.render_shown(field, review),
                                    "at": now_iso_z()}

    row = _write(item_key, review, mutate)
    return {"cleared": False, "feedback": row, "package": _publish(item_key, row, review)}


def delete_part(item_key: str, field: str, reviewed_at: str | None) -> dict[str, Any]:
    """Withdraw one part: its record becomes a ``withdrawn`` tombstone; the row goes only when nothing is left."""
    review = _current_review(item_key, field, reviewed_at)
    changed = False

    def mutate(row: dict[str, Any]) -> None:
        nonlocal changed
        if field == WHOLE:
            changed = bool(row["chips"] or row["note"])
            row["chips"], row["note"] = [], ""
        elif field in row["fields"] and not row["fields"][field].get("withdrawn"):
            changed = True
            row["fields"][field] = {"withdrawn": True, "at": now_iso_z()}

    row = _write(item_key, review, mutate)
    return {"cleared": True, "feedback": row, **({"package": _publish(item_key, row, review)} if changed else {})}


def save(item_key: str, chips: list[str], note: str) -> dict[str, Any]:
    """Whole-review feedback for the current version; no chips and no note withdraws it."""
    if chips or note.strip():
        return put_part(item_key, WHOLE, None, chips, note)
    return delete_part(item_key, WHOLE, None)


def list_feedback(item_key: str) -> list[dict[str, Any]]:
    with closing(repositories._connect_to(settings().triage_db_path)) as conn:
        return store.list_for_paper(conn, item_key)


def export() -> list[dict[str, Any]]:
    """Every feedback row, all papers and review versions, with where its package is and its snapshot status."""
    with closing(repositories._connect_to(settings().triage_db_path)) as conn:
        rows = store.all_rows(conn)
    for row in rows:
        pkg = bundle.package_dir(str(row["item_key"]), str(row["reviewed_at"]), str(row["generation_sha256"]))
        row["package"] = ({"path": _relative(pkg), "snapshot": str(snapshot.read(pkg).get("status") or "")}
                          if pkg.is_dir() else None)
    return rows


def checks(item_key: str) -> dict[str, Any]:
    """What was concluded about the current review's parts (``check.json``); empty when unchecked or invalid."""
    review = _current_review(item_key, WHOLE, None)
    identity = review.get("review_identity") or {}
    pkg = bundle.package_dir(item_key, str(review.get("reviewed_at") or ""), str(identity.get("generation_sha256") or ""))
    return {"item_key": item_key, "reviewed_at": review.get("reviewed_at") or "", "checks": check.read_checks(pkg)}
