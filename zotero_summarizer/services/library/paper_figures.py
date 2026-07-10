"""List an item's already-generated figures for the "attach figures to Zotero"
action — a thin read over the paper-read artifact's figures dir. Kept out of
``paper_render`` (which sits at its 500-LOC ceiling) as a small sibling; reuses
``paper_render``'s state reader + figure-name allowlist so both agree."""
from __future__ import annotations

from pathlib import Path

from zotero_summarizer.services.library.paper_render import _FIGURE_NAME_RE, _read_state


def attachable_figures(item_key: str) -> list[dict[str, str]]:
    """``[{name, path}]`` for the item's generated figures on disk. Scans the
    artifact's figures dir for validated ``fig*`` images (no state ``figures`` list
    needed); empty when the review hasn't been built."""
    state = _read_state(item_key)
    if state is None:
        return []
    figures_dir = Path(str((state.get("outputs") or {}).get("figures_dir") or ""))
    if not figures_dir.is_dir():
        return []
    return [
        {"name": p.name, "path": str(p)}
        for p in sorted(figures_dir.iterdir())
        if p.is_file() and _FIGURE_NAME_RE.match(p.name)
    ]
