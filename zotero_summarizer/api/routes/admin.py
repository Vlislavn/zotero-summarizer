"""Phase 1.18 Step 2 — admin endpoints surfaced in the Settings UI.

Two long-running operations the user can trigger from the React UI:

* ``POST /api/admin/refresh-labels`` — re-export the golden CSV from
  Zotero (calls ``services.goldenset.export_golden_dataset``). Cheap
  (seconds). Synchronous: the caller blocks and gets the result.
* ``POST /api/admin/retrain`` — re-train the classifier gate (calls
  ``services.classifier_persistence.train_and_save``). Expensive
  (minutes). Runs in a background thread; the caller gets a ``job_id``
  and polls ``GET /api/admin/jobs/{job_id}``.

Hybrid GT contract: training overlays ``label_verdicts`` user verdicts
onto the derived CSV labels before fitting. The closed-loop is
*automatic* — clicking "Retrain" makes everything the user typed in
Annotate become ground truth for the next gate.

Single responsibility: orchestration + status. The actual work lives in
``services.goldenset`` and ``services.classifier_persistence``.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.golden import goldenset
from zotero_summarizer.services._common import LOGGER, now_iso, read_config, settings as get_settings


router = APIRouter()


# ---------------------------------------------------------------------------
# In-process job registry
# ---------------------------------------------------------------------------
# A retrain job runs in a background thread. State is kept in-process; if
# the server restarts mid-train the user clicks Retrain again. The thread
# pool stays small (one retrain at a time — guarded by the lock).
_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_RETRAIN_LOCK = threading.Lock()


def _new_job(kind: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "kind": kind,
        "status": "running",
        "started_at": now_iso(),
        "finished_at": None,
        "result": None,
        "error": None,
        "progress": {"done": 0, "total": 0},
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    return job


def _finish_job(job_id: str, *, result: dict[str, Any] | None, error: str | None) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job["status"] = "failed" if error else "succeeded"
        job["finished_at"] = now_iso()
        job["result"] = result
        job["error"] = error


def _set_progress(job_id: str, done: int, total: int) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["progress"] = {"done": int(done), "total": int(total)}


# ---------------------------------------------------------------------------
# Refresh labels (synchronous; cheap)
# ---------------------------------------------------------------------------


async def refresh_labels() -> dict[str, Any]:
    """Re-export the golden CSV from Zotero.

    Synchronous because the export is dominated by SQLite reads on the
    user's Zotero DB and finishes in a few seconds. User verdicts are
    NOT folded in here — the CSV remains the derivation source. Hybrid
    overlay happens at training time via ``hybrid_gt.apply_hybrid``.
    """
    settings = get_settings()
    csv_path = settings.golden_csv_path
    jsonl_path = settings.golden_jsonl_path
    result = goldenset.export_golden_dataset(
        zotero_data_dir=settings.zotero_data_dir,
        output_csv=csv_path,
        output_jsonl=jsonl_path,
        triage_db_path=settings.triage_db_path,
    )
    return {
        "ok": True,
        "finished_at": now_iso(),
        **result,
    }


# ---------------------------------------------------------------------------
# Retrain classifier (async; expensive)
# ---------------------------------------------------------------------------


class RetrainRequest(BaseModel):
    classifier_name: str = Field(
        default="logreg",
        description="One of: logreg | lightgbm | tabpfn.",
    )
    n_folds: int = Field(default=5, ge=2, le=20)


def _retrain_worker(job_id: str, *, classifier_name: str, n_folds: int) -> None:
    """Run training off the event loop. Records progress + outcome.

    Catches the broad-Exception envelope so any failure inside training
    gets surfaced in the job record instead of leaving the user with a
    silent "running forever" job. This is the documented exception to
    the fail-fast rule: a background worker MUST capture its own
    exceptions, since there is no caller to receive them.
    """
    from zotero_summarizer.services.model import classifier_persistence

    settings = get_settings()
    golden_csv = settings.golden_csv_path
    if not golden_csv.exists():
        _finish_job(
            job_id,
            result=None,
            error=f"golden CSV not found at {golden_csv}; click 'Refresh labels' first",
        )
        return
    config = read_config(settings.config_path)

    def progress(done: int, total: int) -> None:
        _set_progress(job_id, done, total)

    with _RETRAIN_LOCK:
        try:
            trained = classifier_persistence.train_and_save(
                golden_csv,
                classifier_name=classifier_name,
                corpus_db_path=settings.corpus_db_path,
                goals_config=config,
                n_folds=n_folds,
                progress_cb=progress,
                triage_db_path=settings.triage_db_path,
                # Write the FAIR run-log so ModelCard shows OOF per-class metrics.
                runs_log_path=settings.data_dir / "classifier-runs.jsonl",
            )
        except Exception as exc:  # pylint: disable=broad-except
            _finish_job(job_id, result=None, error=f"{type(exc).__name__}: {exc}")
            return

    # Hot-swap the freshly-trained gate into the live runtime + re-score the
    # Today slate, so "Retrain" takes effect WITHOUT a server restart (the
    # previous behaviour left the running gate on the old artifact until the
    # next restart). Guarded on the gate being enabled — a disabled gate has no
    # live slot to swap into, so we only persist to disk (loads on next start).
    hot_swap = _hot_swap_after_retrain(trained) if config.classifier_gate.enabled else None

    _finish_job(
        job_id,
        result={
            "classifier_name": trained.classifier_name,
            "n_train": int(trained.training_metadata.get("n_train", 0)),
            "n_holdout": int(trained.training_metadata.get("n_holdout", 0)),
            "thresholds": {
                "keep": round(trained.t_keep, 4),
                "must": round(trained.t_must, 4),
                "could": round(trained.t_could, 4),
            },
            # Surface that the live gate + slate were refreshed (vs. disk-only),
            # so the Settings UI can tell the user Today is already re-ranked.
            "hot_swapped": bool(hot_swap and hot_swap.get("installed")),
            "rescored": (hot_swap or {}).get("rescored"),
        },
        error=None,
    )


def _hot_swap_after_retrain(trained: Any) -> dict[str, Any]:
    """Install the just-trained gate live (atomic swap + slate rescore) via the
    single shared ``feeds.install_gate`` path — the same call the daemon's
    background retrain uses, so both retrain paths converge on one mechanism.

    Installs the in-memory ``trained`` object directly (identical to the joblib
    just written; this also matches the daemon worker, which installs its fresh
    object without a reload — and avoids guessing the artifact filename when the
    retrained classifier differs from the configured gate model_name).

    Best-effort: the model is already trained + saved, so a swap/rescore failure
    is reported in the job result, never raised — it must not turn a successful
    retrain into a failed job.
    """
    from zotero_summarizer.services.triage import feeds

    try:
        result = feeds.install_gate(trained, reason="ui-retrain")
        return {"installed": True, "rescored": (result or {}).get("rescored")}
    except Exception as exc:  # noqa: BLE001 — best-effort; retrain already succeeded
        LOGGER.exception("hot-swap after UI retrain failed (model saved to disk)")
        return {"installed": False, "error": f"{type(exc).__name__}: {exc}"}


async def retrain(req: RetrainRequest) -> dict[str, Any]:
    """Kick off a retrain in the background. Returns the job_id immediately.

    The Settings UI polls ``GET /api/admin/jobs/{job_id}`` until
    ``status`` becomes ``succeeded`` or ``failed``.
    """
    if req.classifier_name not in ("logreg", "lightgbm", "tabpfn"):
        raise APIError(
            error="validation_error",
            message=f"classifier_name must be logreg|lightgbm|tabpfn; got {req.classifier_name!r}",
            status_code=422,
        )
    if _RETRAIN_LOCK.locked():
        raise APIError(
            error="conflict",
            message="another retrain is already running; wait for it to finish",
            status_code=409,
        )

    job = _new_job("retrain")
    thread = threading.Thread(
        target=_retrain_worker,
        kwargs={
            "job_id": job["job_id"],
            "classifier_name": req.classifier_name,
            "n_folds": req.n_folds,
        },
        daemon=True,
    )
    thread.start()
    return {"job_id": job["job_id"], "status": "running"}


# ---------------------------------------------------------------------------
# Job polling
# ---------------------------------------------------------------------------


async def get_job(job_id: str) -> dict[str, Any]:
    """Return the current state of a retrain job. 404 if unknown."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise APIError(
            error="not_found",
            message=f"job {job_id!r} not found (server may have restarted)",
            status_code=404,
        )
    return dict(job)


async def list_jobs() -> dict[str, Any]:
    """Return all in-process jobs (succeeded, failed, or running)."""
    with _JOBS_LOCK:
        rows = sorted(_JOBS.values(), key=lambda j: j["started_at"], reverse=True)
    return {"jobs": rows, "total": len(rows)}


# ---------------------------------------------------------------------------
# Model card — surface the trained classifier's metadata in Settings
# ---------------------------------------------------------------------------


def _model_dir() -> Path:
    """Where ``train_and_save`` writes ``{classifier}.{joblib,json}``."""
    from zotero_summarizer.services.model.classifier_persistence import DEFAULT_MODEL_DIR
    return DEFAULT_MODEL_DIR


def _load_latest_runlog_entry(classifier_name: str) -> dict[str, Any] | None:
    """Return the newest matching entry from ``classifier-runs.jsonl``.

    The classifier writes one JSONL line per train. We scan the whole file
    (it's tiny — append-only) and pick the latest line whose
    ``classifier == classifier_name`` and whose ``type`` is the training
    artefact entry (skip prediction-run lines that share the file).
    """
    settings = get_settings()
    log_path = settings.data_dir / "classifier-runs.jsonl"
    if not log_path.exists():
        return None
    latest: dict[str, Any] | None = None
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("classifier") != classifier_name:
                continue
            # Older entries had no "type" field; treat them as training rows.
            if entry.get("type") not in (None, "train_artifact"):
                continue
            if latest is None or str(entry.get("timestamp", "")) > str(latest.get("timestamp", "")):
                latest = entry
    return latest


async def model_card() -> dict[str, Any]:
    """Return the current trained-classifier metadata for the Settings UI.

    Reads two on-disk sources:
      * ``~/.cache/zotero-summarizer/models/{classifier}.json`` — the JSON
        twin of the joblib artefact (n_train, oof_spearman, trained_at,
        git_commit, sha of the golden CSV used).
      * ``classifier-runs.jsonl`` in the project root — append-only FAIR
        log; the newest matching entry adds CV AUC, per-class metrics,
        thresholds, and the full config.

    Returns ``{"model": null}`` when no model is on disk yet so the UI
    can render an empty state instead of 404'ing.
    """
    md = _model_dir()
    if not md.exists():
        return {"model": None}

    # Find every classifier with a .json twin and pick the freshest by
    # mtime — the user may have multiple (logreg + lightgbm) on disk and
    # we want to surface the one most recently trained.
    candidates: list[tuple[float, Path]] = []
    for json_path in md.glob("*.json"):
        joblib_path = json_path.with_suffix(".joblib")
        if not joblib_path.exists():
            continue
        candidates.append((json_path.stat().st_mtime, json_path))
    if not candidates:
        return {"model": None}
    candidates.sort(reverse=True)
    _, freshest = candidates[0]

    twin = json.loads(freshest.read_text(encoding="utf-8"))
    classifier_name = str(twin.get("classifier_name") or freshest.stem)
    runlog = _load_latest_runlog_entry(classifier_name)

    joblib_path = freshest.with_suffix(".joblib")
    joblib_stat = joblib_path.stat()

    return {
        "model": {
            "classifier_name": classifier_name,
            "trained_at": twin.get("trained_at"),
            "git_commit": twin.get("git_commit"),
            "n_train": twin.get("n_train"),
            "n_positive_library": twin.get("n_positive_library"),
            "feature_dim": twin.get("feature_dim"),
            "objective": twin.get("objective"),
            "oof_spearman": twin.get("oof_spearman"),
            "golden_csv_sha256_prefix": str(twin.get("golden_csv_sha256") or "")[:12],
            "thresholds": twin.get("thresholds") or {},
            "joblib_path": str(joblib_path),
            "joblib_size_bytes": int(joblib_stat.st_size),
            "joblib_mtime": datetime.fromtimestamp(
                joblib_stat.st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "runlog": runlog,  # full JSONL entry; null if no log line found
        },
    }


router.add_api_route("/api/admin/refresh-labels", refresh_labels, methods=["POST"])
router.add_api_route("/api/admin/retrain", retrain, methods=["POST"])
router.add_api_route("/api/admin/jobs/{job_id}", get_job, methods=["GET"])
router.add_api_route("/api/admin/jobs", list_jobs, methods=["GET"])
router.add_api_route("/api/admin/model", model_card, methods=["GET"])
