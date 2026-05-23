"""Shared logger + error type for the Zotero writer (leaf module)."""
from __future__ import annotations

import logging

LOGGER = logging.getLogger("zotero_summarizer.integrations.zotero_write")


class ZoteroWriteError(RuntimeError):
    """Raised when writing to the local Zotero database fails."""
