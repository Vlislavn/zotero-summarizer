"""Bounded current-state reads for the browser's retained Today shortlist."""
from typing import Annotated

from fastapi import APIRouter, Query

from zotero_summarizer.services.triage import daily_members

router = APIRouter()


@router.get("/api/daily/members")
def get_members(item_id: Annotated[list[int], Query(min_length=1, max_length=20)]):
    return daily_members.read_members(item_id)
