from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.models import CalibrationMetricsResponse, CorpusImportResponse
from zotero_summarizer.services import corpus

router = APIRouter()
router.add_api_route("/api/corpus/import", corpus.import_corpus, methods=["POST"], response_model=CorpusImportResponse)
router.add_api_route("/api/corpus/item/{item_key}", corpus.corpus_item_metadata, methods=["GET"])
router.add_api_route("/api/corpus/items", corpus.corpus_items_metadata, methods=["GET"])
router.add_api_route("/api/feedback", corpus.ingest_feedback, methods=["POST"])
router.add_api_route("/api/feedback", corpus.list_feedback, methods=["GET"])
router.add_api_route("/api/calibration/metrics", corpus.calibration_metrics, methods=["GET"], response_model=CalibrationMetricsResponse)
