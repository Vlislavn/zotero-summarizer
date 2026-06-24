"""Phase 1.18 Step 1 — Label-Provenance audit + verdict endpoints.

Surfaces the chain-of-reasoning behind every paper's ``gold_priority_final``
so the user can audit ground-truth labels before trusting any model
metric computed against them, and records per-paper user verdicts that
override the derived label.

Endpoints:
- GET    /api/golden/provenance?item_key=...   single-paper breakdown
- GET    /api/golden/provenance/list           all rows + flag summary
- GET    /api/golden/review-detail?item_key=…  full paper detail + provenance + verdict
- POST   /api/golden/verdict                   record a user verdict (UPSERT per paper)
- GET    /api/golden/verdicts                  list all verdicts (optional filter)
- DELETE /api/golden/verdict?item_key=...      remove a verdict
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.golden import hybrid_gt, label_provenance
from zotero_summarizer.services.library import review_detail as review_detail_svc
from zotero_summarizer.services.zotero.zotero import (
    zotero_set_label_tag,
    zotero_upsert_verdict_note,
)
from zotero_summarizer.storage import repositories
from zotero_summarizer.api.routes._golden_helpers import (
    _append_verdict_golden,
    _build_source_payload,
    _compute_border_into_cache,
    _db_path,
    _golden_csv_path,
    _load_all,
    _zotero_candidate_keys,
    log_retract_event,
    log_verdict_event,
)

LOGGER = logging.getLogger(__name__)


router = APIRouter()


_VALID_USER_PRIORITIES = ("must_read", "should_read", "could_read", "dont_read")


class VerdictRequest(BaseModel):
    item_key: str = Field(..., min_length=1, description="Zotero item key.")
    user_priority: str = Field(
        ..., min_length=1,
        description="One of: must_read | should_read | could_read | dont_read.",
    )
    comment: str = Field(
        default="",
        description="Optional free-text rationale; empty string is allowed.",
    )








async def get_one(item_key: str) -> dict[str, Any]:
    """Return the full provenance breakdown for one paper."""
    provs = _load_all()
    for p in provs:
        if p.item_key == item_key:
            return label_provenance.provenance_to_dict(p)
    raise APIError(
        error="not_found",
        message=f"item_key {item_key!r} not in golden CSV",
        status_code=404,
    )




async def list_all(
    priority: str | None = None,
    flag: str | None = None,
    limit: int = 200,
    collection: str = "",
    tag: str = "",
    search: str = "",
) -> dict[str, Any]:
    """List provenance summaries with optional priority/flag filters, plus
    Zotero collection/tag/search filters (intersected with the provenance set).

    The user's manual verdict (``label_verdicts``) always wins: each row's
    ``effective_priority`` is the user_priority when a verdict exists, else
    the derived/persisted value. Filtering by ``priority`` uses
    ``effective_priority`` so a manually-reclassified paper shows under its
    manual class even after a Refresh-labels re-derivation. Verdicts whose
    key is no longer in the golden CSV are appended as ``orphaned`` rows so
    a manual label is never hidden.
    """
    if not (1 <= int(limit) <= 2000):
        raise APIError(
            error="validation_error",
            message=f"limit must be between 1 and 2000; got {limit}",
            status_code=422,
        )
    provs = _load_all()
    verdicts = hybrid_gt.load_user_verdicts(_db_path())

    def _effective(p) -> str:
        v = verdicts.get(p.item_key)
        return v["user_priority"] if v is not None else p.persisted_priority

    items_all: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for p in provs:
        seen_keys.add(p.item_key)
        v = verdicts.get(p.item_key)
        items_all.append({
            "item_key": p.item_key,
            "title": p.title,
            "persisted_priority": p.persisted_priority,
            "derived_priority": p.derived_priority,
            "effective_priority": _effective(p),
            "user_priority": v["user_priority"] if v is not None else None,
            "is_user_override": bool(v is not None),
            "derived_score": p.derived_score,
            "is_direct_user_verdict": p.is_direct_user_verdict,
            "is_manual_override": p.is_manual_override,
            "orphaned": False,
            "flags": list(p.flags),
        })

    # Orphaned verdicts: a manual label whose paper left the golden CSV.
    # Keep it visible (and editable via the no-404 detail path) so a
    # verdict the user cast can never silently vanish.
    for key, v in verdicts.items():
        if key in seen_keys:
            continue
        items_all.append({
            "item_key": key,
            "title": "(no longer in current set)",
            "persisted_priority": None,
            "derived_priority": None,
            "effective_priority": v["user_priority"],
            "user_priority": v["user_priority"],
            "is_user_override": True,
            "derived_score": None,
            "is_direct_user_verdict": False,
            "is_manual_override": True,
            "orphaned": True,
            "flags": ["orphaned"],
        })

    filtered = items_all
    if priority:
        filtered = [it for it in filtered if it["effective_priority"] == priority]
    if flag:
        filtered = [it for it in filtered if flag in it["flags"]]
    if collection or tag or search:
        candidate_keys = await asyncio.to_thread(
            _zotero_candidate_keys, collection=collection, tag=tag, search=search,
        )
        filtered = [it for it in filtered if it["item_key"] in candidate_keys]

    summary = label_provenance.flag_summary(provs)
    flag_counts = {k: len(v) for k, v in summary.items()}
    flag_counts["orphaned"] = sum(1 for it in items_all if it["orphaned"])
    return {
        "items": filtered[:limit],
        "total_matched": len(filtered),
        "total_rows": len(items_all),
        "flag_counts": flag_counts,
    }






async def review_detail(item_key: str) -> dict[str, Any]:
    """Return the composed verdict-UI payload for one paper.

    Dispatches by key prefix:
      * ``feed:<id>`` -> processed_feed_items + Zotero feedItems lookup
      * ``note:<parent>:<id>`` -> parent Zotero item + the chosen note
      * else (8-char) -> Zotero library item

    The payload shape is uniform across the three sources (see
    ``services/review_detail.py``); the React UI branches on the
    ``source`` field, not on key syntax. Fails 404 only when the
    referenced row is gone from its underlying store.
    """
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(
            error="validation_error",
            message="item_key is required",
            status_code=422,
        )

    provs = _load_all()
    prov_match = next((p for p in provs if p.item_key == safe_item_key), None)
    verdict_row = repositories.get_label_verdict(_db_path(), safe_item_key)

    try:
        source_payload = await asyncio.to_thread(_build_source_payload, safe_item_key)
    except review_detail_svc.InvalidItemKey as exc:
        # A structurally-malformed key (``feed:abc``, ``note:X:notanum``).
        # Surface as 422 rather than an opaque 500.
        raise APIError(
            error="validation_error",
            message=f"item_key {safe_item_key!r} is malformed: {exc}",
            status_code=422,
        ) from exc
    except APIError as exc:
        # Zotero unavailable (503) — degrade to the csv_stub so annotation
        # stays usable. Narrow: only the zotero_unavailable case.
        if exc.error != "zotero_unavailable":
            raise
        source_payload = None

    if source_payload is None:
        # Live source gone — fall back to a stub from the golden CSV row.
        csv_row = await asyncio.to_thread(
            review_detail_svc.load_csv_row, _golden_csv_path(), safe_item_key,
        )
        if csv_row is not None:
            source_payload = review_detail_svc.build_csv_stub_detail(csv_row)
        elif verdict_row is not None:
            # No source row anywhere, but the user cast a manual verdict on
            # this key (e.g. a Today feed item, or a paper that left the
            # engaged set). Never 404 a verdict the user owns — return a
            # minimal stub so it stays viewable + editable + deletable.
            source_payload = review_detail_svc.build_csv_stub_detail(
                {"item_key": safe_item_key, "title": "(no longer in current set)"}
            )
        else:
            raise APIError(
                error="not_found",
                message=f"item_key {safe_item_key!r}: not in any source",
                status_code=404,
            )

    return {
        "item_key": safe_item_key,
        **source_payload,
        "provenance": (
            label_provenance.provenance_to_dict(prov_match)
            if prov_match is not None else None
        ),
        "verdict": verdict_row,
    }


async def submit_verdict(req: VerdictRequest) -> dict[str, Any]:
    """Record a user verdict for one paper. UPSERTs over any prior verdict."""
    if req.user_priority not in _VALID_USER_PRIORITIES:
        raise APIError(
            error="validation_error",
            message=(
                f"user_priority must be one of {_VALID_USER_PRIORITIES}; "
                f"got {req.user_priority!r}"
            ),
            status_code=422,
        )

    # Anchor original_derived_priority to the CURRENT provenance when the
    # key is in the golden CSV; never the client. A key absent from the CSV
    # (a Today feed item, or a paper that left the engaged set) is still
    # labellable — the user's manual verdict must always be saveable. We
    # then anchor to the existing verdict's original (preserve history) or
    # "unknown".
    provs = _load_all()
    prov_match = next((p for p in provs if p.item_key == req.item_key), None)
    if prov_match is not None:
        original = prov_match.derived_priority
    else:
        existing = repositories.get_label_verdict(_db_path(), req.item_key)
        original = existing["original_derived_priority"] if existing is not None else "unknown"

    row_id = repositories.insert_or_update_label_verdict(
        _db_path(),
        item_key=req.item_key,
        original_derived_priority=original,
        user_priority=req.user_priority,
        comment=req.comment,
    )
    log_verdict_event(req.item_key, original, req.user_priority, req.comment)
    # Make the verdict a first-class training row (covers materialized-but-unread
    # items the engagement-only export skips). Idempotent + no-op if source gone.
    # Boundary: the verdict is ALREADY durably saved above — this golden-row
    # enrichment must never block that, so a metadata-fetch failure is logged,
    # not raised (the hybrid overlay still trains any existing row from the
    # verdict). The user authorized "make sure verdicts go to training".
    try:
        await asyncio.to_thread(
            _append_verdict_golden, req.item_key, req.user_priority, req.comment,
        )
    except Exception as exc:  # noqa: BLE001 — verdict save must not fail on enrichment
        LOGGER.warning("golden append for verdict %s failed: %s", req.item_key, exc)

    # The verdict IS the user's explicit label — write it to Zotero as a
    # `label:<priority>` tag so the ground truth lives in Zotero (source of truth,
    # reconciled back on the next export). Library items only: feed:/note: keys
    # have no Zotero item to tag yet and keep the label_verdicts path. Non-blocking
    # + reported-not-raised, exactly like the verdict note below — the verdict is
    # ALREADY durable above. The user authorized "keep my labels inside Zotero".
    label_written = False
    label_error: str | None = None
    source = review_detail_svc.classify_item_key(req.item_key)
    if source not in (review_detail_svc.SOURCE_FEED, review_detail_svc.SOURCE_NOTE):
        try:
            await asyncio.to_thread(zotero_set_label_tag, req.item_key, req.user_priority)
            label_written = True
        except Exception as exc:  # noqa: BLE001 — label write must not block the verdict
            label_error = f"{type(exc).__name__}: {exc}"
            LOGGER.warning("verdict label tag write for %s failed: %s", req.item_key, exc)
    # Save the comment to Zotero as a single (upserted) note. Direct write, but
    # the verdict is ALREADY durable above — a note failure (e.g. Zotero open)
    # must never block it, so it's reported, not raised. The user authorized
    # "comments I leave with a verdict should be saved to Zotero as a note".
    note_written = False
    note_error: str | None = None
    if req.comment.strip():
        try:
            await asyncio.to_thread(
                zotero_upsert_verdict_note, req.item_key, req.user_priority, req.comment,
            )
            note_written = True
        except Exception as exc:  # noqa: BLE001 — note write must not block the verdict
            note_error = f"{type(exc).__name__}: {exc}"
            LOGGER.warning("verdict note write for %s failed: %s", req.item_key, exc)

    stored = repositories.get_label_verdict(_db_path(), req.item_key)
    if stored is None:
        raise RuntimeError(
            f"verdict UPSERT returned id={row_id} but get_label_verdict found nothing"
        )
    return {
        "id": row_id,
        "created_at": stored["created_at"],
        "note_written": note_written,
        "note_error": note_error,
        "label_written": label_written,
        "label_error": label_error,
    }


async def list_verdicts(user_priority: str | None = None) -> dict[str, Any]:
    """List recorded verdicts, optionally filtered by user_priority."""
    if user_priority is not None and user_priority not in _VALID_USER_PRIORITIES:
        raise APIError(
            error="validation_error",
            message=(
                f"user_priority must be one of {_VALID_USER_PRIORITIES}; "
                f"got {user_priority!r}"
            ),
            status_code=422,
        )
    verdicts = repositories.list_label_verdicts(
        _db_path(), user_priority=user_priority
    )
    return {"verdicts": verdicts, "total": len(verdicts)}


async def remove_verdict(item_key: str) -> dict[str, Any]:
    """Delete one verdict; returns whether a row was removed."""
    safe_item_key = str(item_key or "").strip()
    if not safe_item_key:
        raise APIError(
            error="validation_error",
            message="item_key is required",
            status_code=422,
        )
    # Read the prior verdict BEFORE delete so the retraction event keeps the
    # model/human pair the DELETE is about to destroy.
    prior = repositories.get_label_verdict(_db_path(), safe_item_key)
    deleted = repositories.delete_label_verdict(_db_path(), safe_item_key)
    if deleted and prior is not None:
        log_retract_event(safe_item_key, prior)
    return {"deleted": deleted}


async def effective_labels_summary() -> dict[str, Any]:
    """Aggregate counts for the hybrid ground-truth pipeline.

    Phase 1.18 Step 2: surfaces how many CSV rows have a user verdict
    overlaid, and of those, how many changed the derived label vs.
    confirmed it. Powers the "Used as GT" badge and the Settings
    "Effective labels" widget.
    """
    return hybrid_gt.hybrid_summary(_golden_csv_path(), _db_path())


async def effective_labels_list() -> dict[str, Any]:
    """Return the full hybrid ground-truth map.

    Keys are item_key, values carry both the derived and user labels and
    the ``effective_priority`` that ML pipelines use. Useful for the UI
    to mark which rows are user-overridden.
    """
    merged = hybrid_gt.load_hybrid_labels(_golden_csv_path(), _db_path())
    return {
        "items": list(merged.values()),
        "total": len(merged),
    }






async def border_suggestions(top_k: int = 20, refresh: bool = False) -> dict[str, Any]:
    """Active-learning endpoint: library rows whose re-labelling would most
    help the model, ranked by distance to the nearest priority threshold.

    Cached + background-computed (see ``services.border_cache``). Scoring
    every library row is ~1 s/row, so a synchronous compute took >10 min.
    Now:
      * ``status="ready"`` + items — cache hit for the current golden sha.
      * ``status="computing"`` — a background scoring pass is in flight;
        the client should poll.
      * ``status="error"`` — the last background pass failed (message set).

    ``refresh=true`` forces a recompute even when a fresh cache exists.
    """
    from zotero_summarizer.services.library import border_cache
    from zotero_summarizer.services import run_log
    from zotero_summarizer.services.model.classifier_persistence import DEFAULT_MODEL_DIR

    if not (1 <= int(top_k) <= 2000):
        raise APIError(
            error="validation_error",
            message=f"top_k must be between 1 and 2000; got {top_k}",
            status_code=422,
        )

    csv_path = _golden_csv_path()
    if not csv_path.exists():
        raise APIError(
            error="not_found",
            message=f"golden CSV missing at {csv_path}",
            status_code=404,
        )

    golden_sha = run_log.file_sha256(csv_path, prefix_len=64)
    cached = None if refresh else border_cache.read_cache(DEFAULT_MODEL_DIR, golden_sha)
    if cached is not None:
        items = cached["items"][: int(top_k)]
        return {
            "status": "ready",
            "items": items,
            "total": len(items),
            "cached_total": cached.get("total", len(items)),
            "computed_at": cached.get("computed_at"),
        }

    # No fresh cache — ensure a background compute is running.
    if border_cache.try_start():
        border_cache.run_in_background(
            lambda: _compute_border_into_cache(golden_sha, int(top_k))
        )
        return {"status": "computing", "items": [], "total": 0}

    err = border_cache.last_error()
    if err is not None and not border_cache.is_running():
        return {"status": "error", "items": [], "total": 0, "message": err}
    return {"status": "computing", "items": [], "total": 0}


router.add_api_route("/api/golden/provenance", get_one, methods=["GET"])
router.add_api_route("/api/golden/provenance/list", list_all, methods=["GET"])
router.add_api_route("/api/golden/review-detail", review_detail, methods=["GET"])
router.add_api_route("/api/golden/verdict", submit_verdict, methods=["POST"])
router.add_api_route("/api/golden/verdicts", list_verdicts, methods=["GET"])
router.add_api_route("/api/golden/verdict", remove_verdict, methods=["DELETE"])
router.add_api_route(
    "/api/golden/effective-labels/summary",
    effective_labels_summary,
    methods=["GET"],
)
router.add_api_route(
    "/api/golden/border-suggestions",
    border_suggestions,
    methods=["GET"],
)
router.add_api_route(
    "/api/golden/effective-labels",
    effective_labels_list,
    methods=["GET"],
)
