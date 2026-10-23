"""Existing native uncertainty guard; confirmation source follows."""
from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.zotero_actions import ZoteroTargetIdentity
from zotero_summarizer.storage import pending_actions


def check_uncertainty(conn, item_keys: list[str], target: ZoteroTargetIdentity, *,
                      owned_claim: dict | None = None) -> None:
    blockers = pending_actions.applying_blockers(conn, item_keys, target, owned_claim=owned_claim)
    if blockers:
        raise APIError(error="zotero_write_uncertain", message="Earlier delivery needs reconciliation",
                       status_code=409, details={"delivery_status": "uncertain", "blocking_action_ids": blockers})
