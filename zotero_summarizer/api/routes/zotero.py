from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.models import ZoteroCollectionsResponse, ZoteroItemsResponse, ZoteroStatusResponse
from zotero_summarizer.services.zotero import zotero

router = APIRouter()
router.add_api_route("/api/zotero/status", zotero.zotero_status, methods=["GET"], response_model=ZoteroStatusResponse)
router.add_api_route("/api/zotero/collections", zotero.zotero_collections, methods=["GET"], response_model=ZoteroCollectionsResponse)
router.add_api_route("/api/zotero/tags", zotero.zotero_tags, methods=["GET"])
router.add_api_route("/api/zotero/items", zotero.zotero_items, methods=["GET"], response_model=ZoteroItemsResponse)
router.add_api_route("/api/zotero/items/{item_key}", zotero.zotero_item_detail, methods=["GET"])
router.add_api_route("/api/zotero/items/{item_key}/priority", zotero.zotero_set_item_priority, methods=["POST"])
router.add_api_route("/api/zotero/items/{item_key}/tags", zotero.zotero_update_item_tags, methods=["POST"])
router.add_api_route("/api/zotero/items/{item_key}/collections", zotero.zotero_update_item_collections, methods=["POST"])
