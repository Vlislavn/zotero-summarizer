"""Conferences section endpoints — thin over :mod:`services.conferences`.

  GET  /api/conferences                           config overview (+ per-block errors)
  GET  /api/conferences/{id}/{year}               late-breaking + paged all abstracts (no abstract text)
  GET  /api/conferences/{id}/{year}/abstract      one abstract, by ``doi`` (loaded when the row is opened)
  GET  /api/conferences/{id}/{year}/preflight     ≈N requests, source, login — no network
  POST /api/conferences/{id}/{year}/refresh       the only call that reaches the network (explicit click)

Plain ``def`` routes: FastAPI runs them in its thread pool, so the multi-page refresh
does not block the event loop.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from zotero_summarizer.services.conferences import service

router = APIRouter()


@router.get("/api/conferences")
def list_conferences() -> dict[str, Any]:
    return service.overview()


@router.get("/api/conferences/{conf_id}/{year}")
def edition(conf_id: str, year: int, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
            q: str = Query("", max_length=200)) -> dict[str, Any]:
    return service.edition_view(conf_id, year, page=page, page_size=page_size, query=q)


@router.get("/api/conferences/{conf_id}/{year}/abstract")
def abstract(conf_id: str, year: int, doi: str = Query(..., min_length=1)) -> dict[str, str]:
    return {"abstract": service.abstract_of(conf_id, year, doi)}


@router.get("/api/conferences/{conf_id}/{year}/preflight")
def preflight(conf_id: str, year: int) -> dict[str, Any]:
    return service.preflight(conf_id, year)


@router.post("/api/conferences/{conf_id}/{year}/refresh")
def refresh(conf_id: str, year: int) -> dict[str, Any]:
    return service.refresh(conf_id, year)
