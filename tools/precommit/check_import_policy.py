#!/usr/bin/env python3
"""Enforce the layered architecture and the services package structure.

Layering (lower layers must not import higher ones):

    api  ->  services  ->  storage / integrations  ->  models/contracts/domain
    mcp  is a standalone HTTP client (talks to the API over the wire only)

Rules, keyed by the directory a file lives in:
  - integrations/  must not import services or api      (low-level adapters)
  - mcp/           must not import services, api, storage (HTTP client only)
  - storage/       must not import services or api        (persistence only)
  - services/      must not import api.app or api.routes  (api.errors is allowed)

Structure:
  - A new module directly under services/ must be one of the shared modules;
    everything else belongs in a domain subpackage (model/golden/triage/
    library/zotero).
"""
from __future__ import annotations

import pathlib
import re
import sys

# (dir prefix, list of forbidden import prefixes)
LAYER_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("zotero_summarizer/integrations/", ("zotero_summarizer.services", "zotero_summarizer.api")),
    ("zotero_summarizer/mcp/", ("zotero_summarizer.services", "zotero_summarizer.api", "zotero_summarizer.storage")),
    ("zotero_summarizer/storage/", ("zotero_summarizer.services", "zotero_summarizer.api")),
    ("zotero_summarizer/services/", ("zotero_summarizer.api.app", "zotero_summarizer.api.routes")),
]

SHARED_SERVICE_MODULES = {
    "_common", "_adapters", "lifecycle", "run_log",
    "config", "health", "results", "corpus", "emoji_signals", "__init__",
}
SERVICE_DOMAINS = ("model", "golden", "triage", "library", "zotero")

_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+(zotero_summarizer\.[a-zA-Z0-9_.]+)", re.MULTILINE
)


def _imports(text: str) -> list[str]:
    return _IMPORT_RE.findall(text)


def main(paths: list[str]) -> int:
    failures: list[str] = []
    for raw in paths:
        path = pathlib.Path(raw)
        if path.suffix != ".py" or not path.exists():
            continue
        posix = path.as_posix()

        # Layering
        text = path.read_text()
        for prefix, forbidden in LAYER_RULES:
            if not posix.startswith(prefix):
                continue
            for imp in _imports(text):
                for bad in forbidden:
                    if imp == bad or imp.startswith(bad + "."):
                        failures.append(
                            f"{posix}: '{imp}' breaks layering "
                            f"({prefix.rstrip('/').split('/')[-1]} must not import {bad})"
                        )

        # Services structure
        rel = posix.removeprefix("zotero_summarizer/services/")
        if posix.startswith("zotero_summarizer/services/") and "/" not in rel:
            name = path.stem
            if name not in SHARED_SERVICE_MODULES:
                failures.append(
                    f"{posix}: new top-level service module — move it into a domain "
                    f"subpackage ({'/'.join(SERVICE_DOMAINS)}) or add it to the shared set."
                )

    if failures:
        sys.stderr.write("Import / structure policy:\n")
        for f in failures:
            sys.stderr.write(f"  - {f}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
