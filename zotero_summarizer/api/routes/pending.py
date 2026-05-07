from __future__ import annotations

from fastapi import APIRouter

from zotero_summarizer.models import PendingChangesResponse
from zotero_summarizer.services import pending

router = APIRouter()
router.add_api_route("/api/pending", pending.list_pending_changes, methods=["GET"], response_model=PendingChangesResponse)
router.add_api_route("/api/pending/change/{change_id}", pending.update_pending_change, methods=["PUT"])
router.add_api_route("/api/pending/count", pending.pending_change_count, methods=["GET"])
router.add_api_route("/api/pending/override-priority", pending.queue_priority_override, methods=["POST"])
router.add_api_route("/api/pending/reject", pending.reject_pending_changes, methods=["POST"])
router.add_api_route("/api/pending/apply", pending.apply_pending_changes, methods=["POST"])
