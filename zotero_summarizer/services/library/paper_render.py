"""Paper-render-compatible `/paper-read` pipeline for Library papers.

The public contract mirrors the upstream paper-render workflow behavior:
source acquisition (local TeX -> optional arXiv source -> PDF fallback),
Markdown notes, a single-file English HTML "paper brief" (hero, readable
sections, figures, digest), figures next to the PDF, and an audit pass. The
implementation is local and structured for this app; it does not vendor
upstream templates/code.

Note (sanctioned CLAUDE.md rule-4 deviation): the notes/HTML/figures are
written next to the Zotero PDF (`pdf_path.parent`) for upstream compatibility,
not under `data/`; only the `paper_read.json` state lives under
`settings().paper_render_dir`. See `services/library/README.md`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import now_iso_z, settings
from zotero_summarizer.services.library import (
    _paper_read_brief,
    _paper_read_html,
    _paper_read_pdf,
    _paper_read_tex,
    deep_review,
)

LOGGER = logging.getLogger(__name__)

_STATE_FILENAME = "paper_read.json"
_PAPER_READ_VERSION = "paper-read-v1"
_FIGURE_NAME_RE = re.compile(r"^fig\d+_[A-Za-z0-9_.-]+\.(png|jpe?g)$")
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
# Per-item build locks: serialize a sync ensure_artifact against a concurrent
# background /build so the same paper is never built twice (racing figure writes).
_ITEM_LOCKS: dict[str, threading.Lock] = {}
# Bounded pool for background builds (the in-repo faithbench pattern) — replaces
# unbounded raw threads so N concurrent /build requests can't spawn N builds.
_BUILD_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="paper-build")


def _compute_renderer_rev() -> str:
    """Short hash of the renderer source so editing any extractor/HTML module
    invalidates cached artifacts automatically (the cache key folds this in)."""
    digest = hashlib.sha256()
    for module in (_paper_read_brief, _paper_read_html, _paper_read_pdf, _paper_read_tex):
        try:
            digest.update(Path(module.__file__).read_bytes())
        except OSError:  # pragma: no cover - source always readable in practice
            digest.update(module.__name__.encode("utf-8"))
    return digest.hexdigest()[:8]


# Code-derived renderer revision — recomputed at import; changes when any
# renderer module's source changes (P0-1 stale-cache fix).
_RENDERER_REV = _compute_renderer_rev()

# TeX-extracted authors are often garbage (Author 1, Author 2… or bare \cmd residue).
_GARBAGE_AUTHOR_RE = re.compile(r"(?i)\bAuthor\s*\d+|\\[a-zA-Z]")
# Zotero storage folder names are 8-char uppercase alphanumeric keys — not useful as a title.
_ZOTERO_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")


def _format_zotero_authors(raw: Any) -> str:
    """Flatten Zotero creator list (dicts or strings) into a single comma-joined string."""
    if not raw:
        return ""
    names = []
    for a in (raw if isinstance(raw, list) else [raw]):
        if isinstance(a, dict):
            name = " ".join(filter(None, [str(a.get("firstName") or ""), str(a.get("lastName") or "")])).strip()
            if not name:
                name = str(a.get("name") or "").strip()
        else:
            name = str(a).strip()
        if name:
            names.append(name)
    return ", ".join(names)


def _tags_as_keywords(tags: Any) -> list[str]:
    """Convert Zotero tags to a keyword list, filtering out internal zs: and emoji-only tags."""
    if not tags:
        return []
    result = []
    for t in (tags if isinstance(tags, list) else []):
        tag = str(t.get("tag", "") if isinstance(t, dict) else t).strip()
        if not tag or tag.startswith("zs:") or len(tag) > 60:
            continue
        if not any(c.isalpha() for c in tag):  # drop pure-symbol / emoji-only tags
            continue
        result.append(tag)
    return result[:8]


def _render_dir(item_key: str) -> Path:
    return settings().paper_render_dir / item_key


def _state_path(item_key: str) -> Path:
    return _render_dir(item_key) / _STATE_FILENAME


def _read_state(item_key: str) -> dict[str, Any] | None:
    path = _state_path(item_key)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(item_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    out_dir = _render_dir(item_key)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _state_path(item_key)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return payload


def _pdf_for_item(item_key: str) -> tuple[Path, dict[str, Any]]:
    from zotero_summarizer.services.zotero.zotero import get_zotero_reader_or_raise

    reader = get_zotero_reader_or_raise()
    detail = reader.get_item_detail(item_key)
    if detail is None:
        raise APIError(error="not_found", message=f"Item {item_key} not found", status_code=404)
    pdf_path = Path(str(detail.get("pdf_path") or ""))
    if not str(pdf_path) or not pdf_path.is_file():
        raise APIError(error="needs_pdf", message=f"No local PDF for item {item_key}", status_code=404)
    allowed = settings().pdf_root.expanduser().resolve()
    resolved = pdf_path.expanduser().resolve()
    if allowed not in [resolved, *resolved.parents]:
        raise APIError(error="path_not_allowed", message="PDF path is outside configured PDF_ROOT", status_code=403)
    return resolved, detail


def _pdf_key(pdf_path: Path) -> str:
    stat = pdf_path.stat()
    return f"{_PAPER_READ_VERSION}:{_RENDERER_REV}:{int(stat.st_mtime)}:{stat.st_size}"


def _key_is_current(pdf_key: str) -> bool:
    """True when a persisted key was produced by the current renderer revision.

    Old-format keys (`version:mtime:size`, 3 parts) and keys whose renderer-rev
    segment differs are stale → the pane prompts a rebuild."""
    parts = (pdf_key or "").split(":")
    return len(parts) == 4 and parts[0] == _PAPER_READ_VERSION and parts[1] == _RENDERER_REV


def _get_item_lock(item_key: str) -> threading.Lock:
    with _LOCK:
        lock = _ITEM_LOCKS.get(item_key)
        if lock is None:
            lock = threading.Lock()
            _ITEM_LOCKS[item_key] = lock
        return lock


def render_paper(item_key: str) -> dict[str, Any]:
    """Return the build/status payload for one paper-read artifact."""
    with _LOCK:
        job = _JOBS.get(item_key)
        if job is not None and job.get("status") == "running":
            return dict(job)
    state = _read_state(item_key)
    if state is not None:
        if state.get("status") == "completed" and not _key_is_current(str(state.get("pdf_key") or "")):
            # Renderer code changed since this artifact was built → flag for rebuild.
            return {**state, "stale": True}
        return state
    pdf_path, detail = _pdf_for_item(item_key)
    return {
        "status": "missing",
        "item_key": item_key,
        "title": str(detail.get("title") or pdf_path.stem),
        "pdf_path": str(pdf_path),
        "message": "Paper-read artifact has not been built yet.",
    }


def start_build(
    item_key: str, *, force: bool = False, allow_arxiv_source: bool = False
) -> dict[str, Any]:
    """Start a background paper-read build, single-flight per item."""
    with _LOCK:
        running = _JOBS.get(item_key)
        if running is not None and running.get("status") == "running":
            return dict(running)
        payload = {
            "status": "running",
            "item_key": item_key,
            "started_at": now_iso_z(),
            "allow_arxiv_source": allow_arxiv_source,
            "message": "Building paper-read artifact.",
        }
        _JOBS[item_key] = payload

    _BUILD_POOL.submit(
        _build_job, item_key, force=force, allow_arxiv_source=allow_arxiv_source
    )
    return payload


def _build_job(item_key: str, *, force: bool, allow_arxiv_source: bool) -> None:
    try:
        result = build_paper_read(item_key, force=force, allow_arxiv_source=allow_arxiv_source)
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
        _write_state(item_key, payload)
        with _LOCK:
            _JOBS[item_key] = payload


def build_paper_read(
    item_key: str, *, force: bool = False, allow_arxiv_source: bool = False
) -> dict[str, Any]:
    """Build and persist the artifact for a Zotero item.

    Serialized per item so a synchronous ``ensure_artifact`` and a concurrent
    background ``/build`` never build the same paper twice (which would race the
    non-atomic figure writes)."""
    pdf_path, detail = _pdf_for_item(item_key)
    key = _pdf_key(pdf_path)
    with _get_item_lock(item_key):
        existing = _read_state(item_key)
        if existing and not force and existing.get("pdf_key") == key and existing.get("status") == "completed":
            return existing
        artifact = build_paper_read_for_pdf(
            pdf_path,
            title=str(detail.get("title") or ""),
            item_key=item_key,
            allow_arxiv_source=allow_arxiv_source,
            zotero_detail=detail,
        )
        artifact.update({"pdf_key": key, "item_key": item_key})
        return _write_state(item_key, artifact)


def build_paper_read_for_pdf(
    pdf_path: Path,
    *,
    title: str = "",
    item_key: str = "",
    allow_arxiv_source: bool = False,
    zotero_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a paper-read artifact from a PDF path; test-friendly pure facade."""
    pdf_path = pdf_path.expanduser().resolve()
    figures_dir = pdf_path.parent / "figures"
    source_dir = _paper_read_tex.find_local_source(pdf_path)
    arxiv_id = _paper_read_pdf.detect_arxiv_id(pdf_path)
    downloaded_source = None
    if source_dir is None and arxiv_id and allow_arxiv_source:
        downloaded_source = _paper_read_tex.download_arxiv_source(arxiv_id, pdf_path)
        source_dir = downloaded_source

    if source_dir is not None:
        content = _paper_read_tex.parse_tex_source(source_dir, figures_dir)
        source_tier = "local_tex" if downloaded_source is None else "arxiv_tex"
        pdf_content = _paper_read_pdf.extract_pdf_content(pdf_path)
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
        content = _paper_read_pdf.extract_pdf_content(pdf_path)
        content["figures"] = _paper_read_pdf.extract_pdf_figures(pdf_path, figures_dir)
        source_tier = "pdf"

    # P0/P2: prefer Zotero metadata over garbage TeX extraction where it's available
    if zotero_detail:
        if _GARBAGE_AUTHOR_RE.search(str(content.get("authors") or "")) or not content.get("authors"):
            zotero_authors = _format_zotero_authors(zotero_detail.get("authors"))
            if zotero_authors:
                content["authors"] = zotero_authors
        if not content.get("keywords"):
            zotero_kw = _tags_as_keywords(zotero_detail.get("tags"))
            if zotero_kw:
                content["keywords"] = zotero_kw

    # P2: title from Zotero when TeX gives a Zotero storage key or the PDF stem
    if title and (
        not content.get("title")
        or content.get("title") == pdf_path.stem
        or _ZOTERO_KEY_RE.match(str(content.get("title") or ""))
    ):
        content["title"] = title
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
    outputs = _paper_read_html.write_outputs(
        pdf_path, content, digest=digest, quality=quality, goal_summaries=goal_summaries
    )
    content.update(
        {
            "paper_name": outputs["paper_name"],
            "outputs": {
                "notes": outputs["notes_path"],
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


def ensure_artifact(item_key: str) -> dict[str, Any]:
    """Return a completed artifact, building synchronously via local/PDF paths."""
    state = _read_state(item_key)
    if state is not None and state.get("status") == "completed":
        return state
    return build_paper_read(item_key, allow_arxiv_source=False)


def presentation_path(item_key: str) -> Path:
    state = _read_state(item_key)
    if state is None or state.get("status") != "completed":
        raise APIError(error="not_ready", message="Paper-read artifact has not been built", status_code=404)
    path = Path(str((state.get("outputs") or {}).get("presentation") or ""))
    if not path.is_file():
        raise APIError(error="not_found", message="Generated presentation is missing", status_code=404)
    return path


def figure_path(item_key: str, name: str) -> Path:
    """Validated path for a generated figure next to the paper PDF."""
    if not _FIGURE_NAME_RE.match(name or ""):
        raise APIError(error="validation_error", message=f"bad figure name {name!r}", status_code=422)
    state = _read_state(item_key)
    if state is None:
        raise APIError(error="not_ready", message="Paper-read artifact has not been built", status_code=404)
    figures_dir = Path(str((state.get("outputs") or {}).get("figures_dir") or ""))
    path = figures_dir / name
    resolved_dir = figures_dir.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if resolved_dir not in [resolved, *resolved.parents]:
        raise APIError(error="validation_error", message="bad figure path", status_code=422)
    if not resolved.is_file():
        raise APIError(error="not_found", message=f"figure {name} not generated", status_code=404)
    return resolved


def qa_body_text(artifact: dict[str, Any]) -> str:
    """The paper's PDF-extracted body text used for grounding, all tiers.

    ``qa_text`` is the clean PDF extraction persisted for TeX papers (whose own
    ``full_text`` is noisy ``_clean_tex`` output); PDF-tier papers fall back to
    their ``full_text`` (same extraction)."""
    return str(artifact.get("qa_text") or artifact.get("full_text") or "")


def artifact_text(artifact: dict[str, Any], *, max_chars: int) -> str:
    """Comprehensive Q&A context: metadata, generated notes, then paper body."""
    parts = [
        f"Title: {artifact.get('title') or ''}",
        f"Pages: {artifact.get('n_pages') or 0}",
        f"Figures: {artifact.get('figures_count') or 0}",
        f"References: {artifact.get('references_count') or 0}",
    ]
    notes_path = Path(str((artifact.get("outputs") or {}).get("notes") or ""))
    if notes_path.is_file():
        parts.append(notes_path.read_text(encoding="utf-8"))
    body = qa_body_text(artifact)
    if body:
        parts.append(body)
    return "\n\n".join(parts)[:max_chars]
