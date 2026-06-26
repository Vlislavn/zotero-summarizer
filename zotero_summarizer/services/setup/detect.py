"""Read-only probe for likely Zotero data directories (first-run setup).

Surfaced at ``GET /api/setup/detect-zotero`` so the onboarding UI can offer the
user a pick-list instead of making them type a path. Probes the standard per-OS
locations + the path Settings is currently configured with, and reports which
have a ``zotero.sqlite`` / ``storage/`` present.

NEVER writes — every check is a ``Path.exists()``. Ordering puts candidates with
a real DB first so the UI's default pick is the most likely correct one.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from zotero_summarizer.models.setup import DetectedZoteroDir
from zotero_summarizer.services._common import settings


def _platform_candidate_dirs() -> list[Path]:
    """Standard Zotero data-dir locations for the current OS (deduplicated,
    order-preserving). Each is a *candidate* — existence is checked by the
    caller, not here."""
    home = Path.home()
    if sys.platform == "darwin":
        raw = [home / "Zotero", home / "Library" / "Application Support" / "Zotero"]
    elif sys.platform.startswith("win"):
        appdata = os.getenv("APPDATA")
        raw = [home / "Zotero"]
        if appdata:
            raw.append(Path(appdata) / "Zotero" / "Zotero")
    else:  # linux / other posix
        raw = [home / "Zotero", home / ".zotero" / "zotero"]

    seen: set[str] = set()
    out: list[Path] = []
    for path in raw:
        resolved = path.expanduser()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        out.append(resolved)
    return out


def _describe(data_dir: Path, source: str) -> DetectedZoteroDir:
    """Build one candidate row from a data dir. Read-only: only ``exists()``."""
    db_path = data_dir / "zotero.sqlite"
    storage_dir = data_dir / "storage"
    return DetectedZoteroDir(
        data_dir=str(data_dir),
        db_path=str(db_path),
        db_exists=db_path.exists(),
        storage_exists=storage_dir.exists(),
        source=source,
    )


def detect_zotero_data_dirs() -> list[DetectedZoteroDir]:
    """Return candidate Zotero data dirs, those with a real DB first.

    Includes the path Settings is currently configured with (tagged
    ``source="env"``) plus the standard per-OS probe locations (``source="probe"``).
    Deduplicated by ``data_dir`` (the env-tagged entry wins when it collides with
    a probe path, so the user's explicit configuration is never relabelled).
    """
    rows: list[DetectedZoteroDir] = []
    seen: set[str] = set()

    configured = settings().zotero_data_dir
    env_row = _describe(configured, "env")
    rows.append(env_row)
    seen.add(env_row.data_dir)

    for data_dir in _platform_candidate_dirs():
        row = _describe(data_dir, "probe")
        if row.data_dir in seen:
            continue
        seen.add(row.data_dir)
        rows.append(row)

    # Stable sort: candidates with a DB first; the env entry keeps priority
    # within its group because Python's sort is stable and it was inserted first.
    rows.sort(key=lambda r: not r.db_exists)
    return rows
