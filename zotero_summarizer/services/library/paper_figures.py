"""Enumerate generated figure attachments through the paper-serving path checks."""
from __future__ import annotations

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.library.paper_render import _read_state, figure_path


def attachable_figures(item_key: str) -> list[dict[str, str]]:
    """Current audited figures, validated through the same checks as serving."""
    state = _read_state(item_key)
    if state is None:
        return []
    if state.get("status") != "completed":
        raise APIError(error="not_ready", message="Paper-read artifact is not completed", status_code=404)
    # ponytail: reuse serving checks per figure; share a state snapshot if large
    # galleries make these repeated state reads expensive.
    return [
        {"name": f["name"], "path": str(figure_path(item_key, f["name"]))}
        for f in state.get("figures") or [] if f.get("name")
    ]
