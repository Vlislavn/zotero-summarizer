"""Build audited paper briefs in PDF-owned sibling directories.

Only paper_read.json state lives under Settings.paper_render_dir; see README.md.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import now_iso_z, settings, state
from zotero_summarizer.services.library import (
    _paper_read_brief,
    _paper_docling,
    _paper_read_html,
    _paper_read_meta,
    _paper_read_pdf,
    _paper_read_tex,
    deep_review,
)
from zotero_summarizer.services.library._paper_read_meta import qa_body_text  # re-exported

__all__ = ["artifact_text", "qa_body_text"]

LOGGER = logging.getLogger(__name__)

_STATE_FILENAME = "paper_read.json"
_PAPER_READ_VERSION = "paper-read-v2"
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
# Per-item build locks: serialize a synchronous Q&A build against a concurrent
# background /build so the same paper is never built twice (racing figure writes).
_ITEM_LOCKS: dict[str, threading.Lock] = {}
# Bounded pool for background builds (the in-repo faithbench pattern) — replaces
# unbounded raw threads so N concurrent /build requests can't spawn N builds.
_BUILD_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="paper-build")


def artifact_text(artifact: dict[str, Any], *, max_chars: int) -> str:
    """Build comprehensive Q&A context from structured review state + PDF text."""
    item_key = str(artifact.get("item_key") or "")
    review = deep_review.get_cached_review(item_key) if item_key else None
    return _paper_read_meta.artifact_text(artifact, max_chars=max_chars, review=review)


def _compute_renderer_rev() -> str:
    """Short hash of the renderer source so editing any extractor/HTML module
    invalidates cached artifacts automatically (the cache key folds this in)."""
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    for module in (_paper_read_brief, _paper_docling, _paper_read_html, _paper_read_meta, _paper_read_pdf, _paper_read_tex):
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()[:8]


# Code-derived renderer revision — recomputed at import; changes when any
# renderer module's source changes (P0-1 stale-cache fix).
_RENDERER_REV = _compute_renderer_rev()


def _state_path(item_key: str) -> Path:
    if (
        not isinstance(item_key, str) or not item_key.strip() or item_key in {".", ".."}
        or any(c in item_key for c in "/\\\0")
    ):
        raise APIError(error="validation_error", message="Invalid paper item key", status_code=422)
    path = settings().paper_render_dir.resolve() / item_key / _STATE_FILENAME
    # Reject links to sibling items too, not just links outside the configured root.
    if any(p.is_symlink() for p in (path.parent, path, path.with_suffix(".tmp"))):
        raise APIError(error="validation_error", message="Paper state symlinks are not allowed", status_code=422)
    return path


def _read_state(item_key: str) -> dict[str, Any] | None:
    path = _state_path(item_key)
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("status") == "completed" and not _paper_read_html._audit_passed(state.get("audit")):
        state.update(status="error", error="paper_audit_failed",
                     message="Paper audit is blocking or unverified; rebuild the artifact.")
    return state


def _write_state(item_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = _state_path(item_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return payload


def _item_detail(item_key: str) -> dict[str, Any]:
    # Key-aware: a stable_feed_key (un-materialized Today paper) resolves via the app
    # library reader even when live Zotero is present; a Zotero key uses the live reader.
    # _pdf_for_item_or_acquire then fetches the PDF into the local cache for a feed item.
    from zotero_summarizer.services.zotero.zotero import resolve_reader_for_key

    reader = resolve_reader_for_key(item_key)
    detail = reader.get_item_detail(item_key)
    if detail is None:
        raise APIError(error="not_found", message=f"Item {item_key} not found", status_code=404)
    return detail


def _pdf_from_detail(item_key: str, detail: dict[str, Any]) -> Path:
    pdf_path = Path(str(detail.get("pdf_path") or ""))
    if not str(pdf_path) or not pdf_path.is_file():
        raise APIError(error="needs_pdf", message=f"No local PDF for item {item_key}", status_code=404)
    return _allowed_paper_path(pdf_path)


def _pdf_for_item_or_acquire(item_key: str, *, allow_acquire_missing: bool) -> tuple[Path, dict[str, Any]]:
    """Return a local PDF path, optionally acquiring one into the fetch cache.

    The normal paper-render path uses Zotero's attached PDF. The Library brief
    shortcut can opt into the same acquisition chain the deep-review path uses:
    arXiv/OA first, then the university/Chrome browser session. Acquired PDFs are
    rendered from the cache and are not written back into Zotero.
    """
    detail = _item_detail(item_key)
    try:
        return _pdf_from_detail(item_key, detail), detail
    except APIError as exc:
        if exc.error != "needs_pdf":
            raise
        saved = _read_state(item_key)
        if saved and saved.get("acquired_pdf"):
            cached = _allowed_paper_path(saved.get("pdf_path"))
            if cached.is_file():
                return cached, {**detail, "pdf_path": str(cached), "has_pdf": True, "acquired_pdf": True}
        if not allow_acquire_missing:
            raise

    from zotero_summarizer.services.library import _pdf_acquire  # lazy: browser stack is optional

    acquired = _pdf_acquire.acquire_pdf_for(item_key, detail, allow_headed_fallback=True)
    if acquired.path is not None:
        path = _allowed_paper_path(acquired.path)
        if not path.is_file():
            raise APIError(
                error="needs_pdf",
                message=f"PDF acquisition for item {item_key} returned a missing file",
                status_code=404,
            )
        return path, {**detail, "pdf_path": str(path), "has_pdf": True, "acquired_pdf": True}
    if acquired.needs_login:
        raise APIError(
            error="needs_library_login",
            message=(
                "The paper has no local Zotero PDF, and the browser/university "
                "session could not fetch it. Open the publisher page in Chrome or "
                "refresh University access, then retry."
            ),
            status_code=424,
            details={"login_url": acquired.login_url},
        )
    if acquired.outcome == "browser_extra_unavailable":
        raise APIError(
            error="browser_extra_unavailable",
            message="Browser support is not installed; open Settings → University access.",
            status_code=424,
        )
    raise APIError(
        error="needs_pdf",
        message=f"No local PDF or fetchable full-text source for item {item_key}.",
        status_code=404,
    )


def _use_docling() -> bool:
    app = state().app_state
    return app.config.quality_review.use_docling if app is not None else False


def _pdf_key(pdf_path: Path) -> str:
    with pdf_path.open("rb") as source:
        content = hashlib.file_digest(source, "sha256").hexdigest()
    identity = hashlib.sha256(f"{pdf_path.resolve()}\0{content}".encode()).hexdigest()
    return f"{_PAPER_READ_VERSION}:{_RENDERER_REV}:{identity}:docling={_use_docling()}"


def _completed_outputs_missing(state: dict[str, Any]) -> bool:
    """True when a completed state points at generated files that no longer exist."""
    if state.get("status") != "completed":
        return False
    outputs = state.get("outputs") or {}
    presentation = Path(str(outputs.get("presentation") or ""))
    return not str(presentation) or not presentation.is_file()


def _get_item_lock(item_key: str) -> threading.Lock:
    with _LOCK:
        lock = _ITEM_LOCKS.get(item_key)
        if lock is None:
            lock = threading.Lock()
            _ITEM_LOCKS[item_key] = lock
        return lock


def render_paper(item_key: str) -> dict[str, Any]:
    """Return the build/status payload for one paper-read artifact."""
    _state_path(item_key)
    with _LOCK:
        job = _JOBS.get(item_key)
        if job is not None and job.get("status") == "running":
            return dict(job)
    state = _read_state(item_key)
    if state is not None:
        if state.get("status") == "completed":
            if _completed_outputs_missing(state):
                return {
                    **state,
                    "status": "missing",
                    "stale": True,
                    "message": "Generated HTML brief is missing; rebuild the paper-read artifact.",
                }
            pdf_path, _detail = _pdf_for_item_or_acquire(item_key, allow_acquire_missing=False)
            if state.get("pdf_key") != _pdf_key(pdf_path):
                return {**state, "stale": True}
        return state
    try:
        pdf_path, detail = _pdf_for_item_or_acquire(item_key, allow_acquire_missing=False)
    except APIError as exc:
        if exc.error != "needs_pdf":
            raise
        detail = _item_detail(item_key)
        return {
            "status": "missing",
            "item_key": item_key,
            "title": str(detail.get("title") or item_key),
            "needs_pdf": True,
            "message": (
                "No local Zotero PDF is attached. Building the brief can try the "
                "browser/university full-text acquisition path."
            ),
        }
    return {
        "status": "missing",
        "item_key": item_key,
        "title": str(detail.get("title") or pdf_path.stem),
        "pdf_path": str(pdf_path),
        "message": "Paper-read artifact has not been built yet.",
    }


def start_build(
    item_key: str, *, force: bool = False, allow_arxiv_source: bool = False,
    allow_acquire_missing: bool = False,
) -> dict[str, Any]:
    """Start a background paper-read build, single-flight per item."""
    _state_path(item_key)
    with _LOCK:
        running = _JOBS.get(item_key)
        if running is not None and running.get("status") == "running":
            return dict(running)
        payload = {
            "status": "running",
            "item_key": item_key,
            "started_at": now_iso_z(),
            "allow_arxiv_source": allow_arxiv_source,
            "allow_acquire_missing": allow_acquire_missing,
            "message": "Building paper-read artifact.",
        }
        _JOBS[item_key] = payload

    _BUILD_POOL.submit(
        _build_job, item_key, force=force, allow_arxiv_source=allow_arxiv_source,
        allow_acquire_missing=allow_acquire_missing,
    )
    return payload


def _build_job(
    item_key: str, *, force: bool, allow_arxiv_source: bool, allow_acquire_missing: bool
) -> None:
    try:
        result = build_paper_read(
            item_key,
            force=force,
            allow_arxiv_source=allow_arxiv_source,
            allow_acquire_missing=allow_acquire_missing,
        )
        with _LOCK:
            _JOBS[item_key] = result
    except Exception as exc:  # noqa: BLE001 - background boundary
        LOGGER.exception("paper-read build failed for %s", item_key)
        payload = {
            "status": "error",
            "item_key": item_key,
            "error": f"{type(exc).__name__}: {exc}",
            "completed_at": now_iso_z(),
        }
        if isinstance(exc, APIError):
            payload.update(error=exc.error, message=exc.message, details=exc.details)
        _write_state(item_key, payload)
        with _LOCK:
            _JOBS[item_key] = payload
        raise


def build_paper_read(
    item_key: str, *, force: bool = False, allow_arxiv_source: bool = False,
    allow_acquire_missing: bool = False,
) -> dict[str, Any]:
    """Build and persist the artifact for a Zotero item.

    Serialized per item so a synchronous Q&A build and a concurrent
    background ``/build`` never build the same paper twice (which would race the
    non-atomic figure writes)."""
    _state_path(item_key)
    with _get_item_lock(item_key):
        pdf_path, detail = _pdf_for_item_or_acquire(
            item_key, allow_acquire_missing=allow_acquire_missing
        )
        key = _pdf_key(pdf_path)
        existing = _read_state(item_key)
        if (
            existing
            and not force
            and existing.get("pdf_key") == key
            and existing.get("status") == "completed"
            and not _completed_outputs_missing(existing)
        ):
            return existing
        artifact = build_paper_read_for_pdf(
            pdf_path,
            title=str(detail.get("title") or ""),
            item_key=item_key,
            allow_arxiv_source=allow_arxiv_source,
            zotero_detail=detail,
        )
        if _pdf_key(pdf_path) != key:
            raise APIError(error="source_changed", message="PDF or parser setting changed during the build; retry", status_code=409)
        artifact.update({"pdf_key": key, "item_key": item_key})
        if detail.get("acquired_pdf"):
            artifact["acquired_pdf"] = True
        return _write_state(item_key, artifact)


def _paper_directory(pdf_path: Path) -> Path:
    """One bounded, title-independent sibling directory per resolved PDF name."""
    identity = hashlib.sha256(os.fsencode(pdf_path.name)).hexdigest()
    directory = pdf_path.parent / f"{pdf_path.stem[:32]}-{identity}.paper-read"
    # Reject existing aliases before any source reads or artifact writes.
    # ponytail: one tree scan per build; descriptor-relative I/O if hostile local races enter scope.
    if directory.is_symlink() or any(path.is_symlink() for path in directory.rglob("*")):
        raise APIError(error="validation_error", message="Paper artifact symlinks are not allowed", status_code=422)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def build_paper_read_for_pdf(
    pdf_path: Path,
    *,
    title: str = "",
    item_key: str = "",
    allow_arxiv_source: bool = False,
    zotero_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a paper-read artifact using the application's selected PDF parser."""
    pdf_path = pdf_path.expanduser().resolve()
    directory = _paper_directory(pdf_path)
    source_dir = directory / "source"
    if not any(path.is_file() for path in source_dir.rglob("*.tex")):
        source_dir = None
    arxiv_id = _paper_read_pdf.detect_arxiv_id(pdf_path)
    downloaded_source = None
    if source_dir is None and arxiv_id and allow_arxiv_source:
        downloaded_source = _paper_read_tex.download_arxiv_source(arxiv_id, directory / "source")
        source_dir = downloaded_source

    with TemporaryDirectory(dir=directory, prefix=".paper-build-") as staging:
        figures_dir = Path(staging) / "figures"
        pdf_content = _paper_read_pdf.extract_pdf_content(pdf_path, use_docling=_use_docling())
        if source_dir is not None:
            content = _paper_read_tex.parse_tex_source(source_dir, figures_dir)
            source_tier = "local_tex" if downloaded_source is None else "arxiv_tex"
            content["n_pages"] = pdf_content["n_pages"]
            # Q&A + section rendering use the cleaner PDF extraction, not noisy TeX
            # (cleaned TeX leaks math/markup). PDF body text grounds comprehensive
            # Q&A on TeX papers; PDF sections render as the readable "brief" body.
            content["qa_text"] = pdf_content.get("full_text") or ""
            content["render_sections"] = pdf_content.get("sections") or []
            # P0: TeX figure resolution often fails → fall back to PDF region crops
            if not [f for f in content.get("figures") or [] if f.get("name")]:
                pdf_figs = _paper_read_pdf.extract_pdf_figures(pdf_path, figures_dir)
                if pdf_figs:
                    content["figures"] = pdf_figs
        else:
            content = pdf_content
            content["figures"] = _paper_read_pdf.extract_pdf_figures(pdf_path, figures_dir)
            source_tier = "pdf"

        # P0/P2: prefer Zotero metadata (authors/keywords/title) over garbage TeX extraction
        _paper_read_meta.apply_metadata_fallbacks(
            content, zotero_detail=zotero_detail, title=title, pdf_stem=pdf_path.stem
        )
        content.update(
            {
                "status": "completed",
                "source_tier": source_tier,
                "source_dir": str(source_dir) if source_dir is not None else "",
                "arxiv_id": arxiv_id or "",
                "pdf_path": str(pdf_path),
                "built_at": now_iso_z(),
            }
        )
        cached = deep_review.get_cached_review(item_key) if item_key else None
        digest = cached["digest"] if cached and cached.get("digest") else None
        quality = cached.get("quality") if cached else None
        goal_summaries = cached.get("goal_summaries") if cached else None
        code_link = cached.get("code_link") if cached else None
        outputs = _paper_read_html.write_outputs(
            directory, content, staging=Path(staging), digest=digest, quality=quality,
            goal_summaries=goal_summaries, code_link=code_link,
        )
        content.update(
            {
                "outputs": {
                    "presentation": outputs["presentation_path"],
                    "audit": outputs["audit_path"],
                    "figures_dir": outputs["figures_dir"],
                    "source_dir": content.get("source_dir", ""),
                },
                "audit": outputs["audit"],
                "sections_count": len(content.get("sections") or []),
                "figures_count": len([f for f in content.get("figures") or [] if f.get("name")]),
                "references_count": int(content.get("references_count") or 0),
            }
        )
        return content


def _allowed_paper_path(path: str | Path | None) -> Path:
    if not path:
        raise APIError(error="not_found", message="Paper artifact path is missing", status_code=404)
    resolved = Path(path).expanduser().resolve()
    roots = (settings().pdf_root, settings().pdf_cache_dir)
    if not any(resolved.is_relative_to(root.expanduser().resolve()) for root in roots):
        raise APIError(error="path_not_allowed", message="Paper path is outside allowed roots", status_code=403)
    return resolved


def presentation_path(item_key: str) -> Path:
    state = _read_state(item_key)
    if state is None or state.get("status") != "completed":
        raise APIError(error="not_ready", message="Paper-read artifact has not been built", status_code=404)
    path = _allowed_paper_path((state.get("outputs") or {}).get("presentation"))
    if not path.is_file():
        raise APIError(error="not_found", message="Generated presentation is missing", status_code=404)
    return path


def source_pdf_path(item_key: str) -> Path:
    """Validated path to the PDF that the paper-read artifact was built from.

    This may be Zotero's attached PDF or a browser/OA-acquired cache PDF. It is
    served through the API so the UI can link to it without exposing a local file
    URL.
    """
    state = _read_state(item_key)
    if state is None or state.get("status") != "completed":
        raise APIError(error="not_ready", message="Paper-read artifact has not been built", status_code=404)
    path = _allowed_paper_path(state.get("pdf_path"))
    if not path.is_file():
        raise APIError(error="not_found", message="Source PDF for this brief is missing", status_code=404)

    return path


def figure_path(item_key: str, name: str) -> Path:
    """Validated path for a generated figure next to the paper PDF."""
    if not _paper_read_html._FIGURE_NAME_RE.fullmatch(name or ""):
        raise APIError(error="validation_error", message=f"bad figure name {name!r}", status_code=422)
    state = _read_state(item_key)
    if state is None or state.get("status") != "completed":
        raise APIError(error="not_ready", message="Paper-read artifact has not been built", status_code=404)
    if name not in {f.get("name") for f in state.get("figures") or []}:
        raise APIError(error="not_found", message=f"figure {name} not in audited artifact", status_code=404)
    figures_dir = _allowed_paper_path((state.get("outputs") or {}).get("figures_dir"))
    resolved = _allowed_paper_path(figures_dir / name)
    if not resolved.is_relative_to(figures_dir):
        raise APIError(error="validation_error", message="bad figure path", status_code=422)
    if not resolved.is_file():
        raise APIError(error="not_found", message=f"figure {name} not generated", status_code=404)
    return resolved
