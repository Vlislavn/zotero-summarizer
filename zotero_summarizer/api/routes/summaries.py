from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.models import BatchSummarizeResponse, SummarizeResponse
from zotero_summarizer.services import summarization

router = APIRouter()
router.add_api_route("/api/summaries", summarization.summarize, methods=["POST"], response_model=SummarizeResponse)
router.add_api_route("/api/summaries/batch", summarization.batch_summarize, methods=["POST"], response_model=BatchSummarizeResponse)
