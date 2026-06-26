"""Phase 1.16 Step 0.2 — endpoints for the test-retest reliability study.

Six endpoints:
- POST /api/relabel-audit/init     start (or resume) a session
- GET  /api/relabel-audit/next     fetch the next unanswered candidate (blind)
- POST /api/relabel-audit/{key}    submit verdict for one paper
- GET  /api/relabel-audit/status   count of answered/remaining
- GET  /api/relabel-audit/metrics  Cohen's kappa + ICC + Pearson + Spearman
- POST /api/relabel-audit/reset    delete the session (start over)

The session is persisted as JSON on disk at
``<project_root>/data/relabel-audit-session.json``. The original label is never
sent in the ``/next`` payload — only `title + authors + venue + abstract`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.golden import relabel_audit
from zotero_summarizer.services._common import settings as get_settings

router = APIRouter()


SESSION_FILENAME = "relabel-audit-session.json"


def _session_path():
    return get_settings().data_dir / SESSION_FILENAME


def _golden_csv_path():
    return get_settings().golden_csv_path


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
    "/api/relabel-audit/metrics", get_metrics, methods=["GET"],
)
router.add_api_route(
    "/api/relabel-audit/reset", reset, methods=["POST"],
)
