#!/usr/bin/env python3
"""Enforce the layered architecture and the services package structure.

Layering (lower layers must not import higher ones):

    api  ->  services  ->  storage / integrations  ->  models/contracts/domain
    mcp  is a standalone HTTP client (talks to the API over the wire only)

Rules, keyed by the directory a file lives in:
  - integrations/  must not import services or api      (low-level adapters)
  - mcp/           must not import services, api, storage (HTTP client only)
  - storage/       must not import services or api        (persistence only)
  - services/      may import api.errors only within the API layer

Structure:
  - A new module directly under services/ must be one of the shared modules;
    everything else belongs in a domain subpackage (model/golden/triage/
    library/zotero).
"""
from __future__ import annotations

import ast
from importlib.util import resolve_name
import pathlib
import sys

# (dir prefix, list of forbidden import prefixes)
LAYER_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("zotero_summarizer/integrations/", ("zotero_summarizer.services", "zotero_summarizer.api")),
    ("zotero_summarizer/mcp/", ("zotero_summarizer.services", "zotero_summarizer.api", "zotero_summarizer.storage")),
    ("zotero_summarizer/storage/", ("zotero_summarizer.services", "zotero_summarizer.api")),
    ("zotero_summarizer/services/", ("zotero_summarizer.api",)),
]

SHARED_SERVICE_MODULES = {
    "_common", "_adapters", "lifecycle", "run_log", "interaction_log",
    "config", "config_overrides", "health", "readiness", "results", "corpus",
    "emoji_signals", "__init__",
}
SERVICE_DOMAINS = ("model", "golden", "triage", "library", "zotero")

def _imports(text: str, path: pathlib.Path) -> list[str]:
    """Resolve static imports without executing modules, including nested imports."""
    package = ".".join(path.parent.parts)
    imports: list[str] = []
    for node in ast.walk(ast.parse(text, filename=str(path))):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = resolve_name("." * node.level + (node.module or ""), package)
            imports.extend(f"{module}.{alias.name}" for alias in node.names)
    return imports


def main(paths: list[str]) -> int:
    failures: list[str] = []
    for raw in paths:
        path = pathlib.Path(raw)
        if path.suffix != ".py" or not path.exists():
            continue
        path = path.resolve().relative_to(pathlib.Path.cwd())
        posix = path.as_posix()
        if not posix.startswith("zotero_summarizer/"):
            continue

        # Layering
        text = path.read_text()
        try:
            imports = _imports(text, path)
        except (SyntaxError, ImportError) as exc:
            failures.append(f"{posix}: cannot analyze imports: {exc}")
            continue
        for prefix, forbidden in LAYER_RULES:
            if not posix.startswith(prefix):
                continue
            for imp in imports:
                if prefix == "zotero_summarizer/services/" and (
                    imp == "zotero_summarizer.api.errors" or imp.startswith("zotero_summarizer.api.errors.")
                ):
                    continue
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
