"""Phase 1.16 Step 0.2 — endpoints for the test-retest reliability study.

Six endpoints:
- POST /api/relabel-audit/init     start (or resume) a session
- GET  /api/relabel-audit/next     fetch the next unanswered candidate (blind)
- POST /api/relabel-audit/{key}    submit verdict for one paper
- GET  /api/relabel-audit/status   count of answered/remaining
- GET  /api/relabel-audit/metrics  Cohen's kappa + ICC + Pearson + Spearman
- POST /api/relabel-audit/reset    delete the session (start over)

The session is persisted as JSON on disk at
``<project_root>/relabel-audit-session.json``. The original label is never
sent in the ``/next`` payload — only `title + authors + venue + abstract`.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services import relabel_audit
from zotero_summarizer.services._common import settings as get_settings

router = APIRouter()


SESSION_FILENAME = "relabel-audit-session.json"


def _session_path():
    return get_settings().project_root / SESSION_FILENAME


def _golden_csv_path():
    return get_settings().project_root / "zotero-summarizer-golden.csv"


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class InitRequest(BaseModel):
    sample_size: int = Field(
        default=100, ge=10, le=500,
        description="Target sample size (best-effort within the eligible pool).",
    )
    seed: int = Field(default=42, description="Sampling seed for reproducibility.")
    resume_if_exists: bool = Field(
        default=True,
        description=(
            "If true and a session already exists on disk, return its summary "
            "without overwriting. Set false to start over."
        ),
    )


class SubmitRequest(BaseModel):
    new_priority: str = Field(
        ...,
        description="One of must_read | should_read | could_read | dont_read",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _blind_candidate_payload(cand: dict[str, Any]) -> dict[str, Any]:
    """Strip the original verdict and relevance from the UI payload."""
    return {
        "item_key": cand["item_key"],
        "title": cand["title"],
        "authors": cand["authors"],
        "venue": cand["venue"],
        "abstract": cand["abstract"],
        "days_since_added": cand["days_since_added"],
        "age_bucket": cand["age_bucket"],
    }


def _session_summary(session: dict[str, Any]) -> dict[str, Any]:
    from collections import Counter

    candidates = session.get("candidates") or []
    responses = session.get("responses") or {}
    bucket_counts: Counter[str] = Counter(c["age_bucket"] for c in candidates)
    class_counts: Counter[str] = Counter(c["original_priority"] for c in candidates)
    return {
        "created_at": session.get("created_at"),
        "sample_size_target": session.get("sample_size"),
        "sample_size_actual": len(candidates),
        "answered": len(responses),
        "remaining": len(candidates) - len(responses),
        "by_age_bucket": dict(bucket_counts),
        "by_original_class": dict(class_counts),
        "seed": session.get("seed"),
    }


async def init_audit(body: InitRequest) -> dict[str, Any]:
    """Create or resume the relabel-audit session."""
    path = _session_path()
    if path.exists() and body.resume_if_exists:
        return _session_summary(relabel_audit.read_session(path))

    rows = relabel_audit.load_golden_rows(_golden_csv_path())
    chosen = relabel_audit.sample_stratified(
        rows, sample_size=body.sample_size, seed=body.seed,
    )
    relabel_audit.write_session(
        path, chosen, sample_size=body.sample_size, seed=body.seed,
    )
    return _session_summary(relabel_audit.read_session(path))


async def get_next() -> dict[str, Any]:
    """Return the next unanswered candidate (blind: original label hidden)."""
    path = _session_path()
    if not path.exists():
        raise APIError(
            error="no_session",
            message="No audit session exists; call POST /api/relabel-audit/init first.",
            status_code=404,
        )
    session = relabel_audit.read_session(path)
    answered = set((session.get("responses") or {}).keys())
    for cand in session["candidates"]:
        if cand["item_key"] not in answered:
            return {"candidate": _blind_candidate_payload(cand)}
    return {"candidate": None, "message": "All candidates labeled. Call /metrics."}


async def submit(item_key: str, body: SubmitRequest) -> dict[str, Any]:
    """Record one re-label verdict."""
    path = _session_path()
    if not path.exists():
        raise APIError(
            error="no_session",
            message="No audit session exists; call POST /api/relabel-audit/init first.",
            status_code=404,
        )
    session = relabel_audit.record_response(path, item_key, body.new_priority)
    return _session_summary(session)


async def get_status() -> dict[str, Any]:
    """Summarize the session (no candidate payloads — count only)."""
    path = _session_path()
    if not path.exists():
        return {"exists": False}
    summary = _session_summary(relabel_audit.read_session(path))
    summary["exists"] = True
    return summary


async def get_metrics() -> dict[str, Any]:
    """Compute κ, ICC, Pearson, Spearman over the responses collected so far."""
    path = _session_path()
    if not path.exists():
        raise APIError(
            error="no_session",
            message="No audit session exists; call POST /api/relabel-audit/init first.",
            status_code=404,
        )
    session = relabel_audit.read_session(path)
    responses = relabel_audit.responses_from_session(session)
    if not responses:
        raise APIError(
            error="no_responses",
            message="Session has zero re-labels yet — submit at least one verdict first.",
            status_code=400,
        )
    metrics = relabel_audit.compute_metrics(responses)
    return relabel_audit.metrics_to_dict(metrics)


async def reset() -> dict[str, Any]:
    """Delete the session file. The next call to /init will create a fresh one."""
    path = _session_path()
    if path.exists():
        path.unlink()
    return {"deleted": True, "path": str(path)}


async def get_trickle(max_per_day: int = 2) -> dict[str, Any]:
    """Return up to ``max_per_day`` blind audit candidates (Phase 1.17 Step 3).

    ``rate_limited`` is ``True`` when zero candidates are returned but the
    unanswered pool is non-empty — i.e., the 24h-since-last-response gate is
    still active. Strips ``original_priority`` / ``original_inferred_relevance``
    from the payload (same blind rule as ``/next``).
    """
    if not (1 <= int(max_per_day) <= 5):
        raise APIError(
            error="validation_error",
            message=f"max_per_day must be between 1 and 5 inclusive; got {max_per_day}",
            status_code=422,
        )
    path = _session_path()
    if not path.exists():
        raise APIError(
            error="no_session",
            message="No audit session exists; call POST /api/relabel-audit/init first.",
            status_code=404,
        )
    candidates = relabel_audit.next_audit_for_today(
        path, max_per_day=int(max_per_day),
    )
    if candidates:
        normalized: list[dict[str, Any]] = []
        for cand in candidates:
            if isinstance(cand, dict):
                normalized.append(cand)
            elif is_dataclass(cand):
                normalized.append(asdict(cand))
            else:
                raise TypeError(
                    f"next_audit_for_today returned unsupported type {type(cand).__name__}"
                )
        return {
            "candidates": [_blind_candidate_payload(cand) for cand in normalized],
            "rate_limited": False,
        }

    session = relabel_audit.read_session(path)
    answered = set((session.get("responses") or {}).keys())
    unanswered_remaining = any(
        c["item_key"] not in answered for c in session["candidates"]
    )
    return {"candidates": [], "rate_limited": unanswered_remaining}


router.add_api_route(
    "/api/relabel-audit/init", init_audit, methods=["POST"],
)
router.add_api_route(
    "/api/relabel-audit/next", get_next, methods=["GET"],
)
router.add_api_route(
    "/api/relabel-audit/{item_key}", submit, methods=["POST"],
)
router.add_api_route(
    "/api/relabel-audit/status", get_status, methods=["GET"],
)
router.add_api_route(
    "/api/relabel-audit/metrics", get_metrics, methods=["GET"],
)
router.add_api_route(
    "/api/relabel-audit/reset", reset, methods=["POST"],
)
router.add_api_route(
    "/api/relabel-audit/trickle", get_trickle, methods=["GET"],
)
