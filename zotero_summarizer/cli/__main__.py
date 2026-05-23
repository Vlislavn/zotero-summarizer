"""Enable ``python -m zotero_summarizer.cli`` (the package entry point)."""
from __future__ import annotations

import sys

from zotero_summarizer.cli import main

raise SystemExit(main(sys.argv[1:]))
