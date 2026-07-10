"""Regenerate ``docs/overrides.md`` from the ``ZS_*`` override registry.

Single source of truth = ``services/config_overrides.REGISTRY``. Run after adding a
system-owned config field:  ``python tools/gen_overrides_doc.py``.
``tests/test_config_overrides.py`` asserts the committed file matches the registry.
"""
from __future__ import annotations

from pathlib import Path

from zotero_summarizer.services.config_overrides import render_overrides_doc

DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "overrides.md"


def main() -> None:
    DOC_PATH.write_text(render_overrides_doc(), encoding="utf-8")
    print(f"wrote {DOC_PATH}")


if __name__ == "__main__":
    main()
