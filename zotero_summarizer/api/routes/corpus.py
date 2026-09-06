from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.services import corpus

router = APIRouter()
router.add_api_route("/api/corpus/item/{item_key}", corpus.corpus_item_metadata, methods=["GET"])
router.add_api_route("/api/corpus/items", corpus.corpus_items_metadata, methods=["GET"])
