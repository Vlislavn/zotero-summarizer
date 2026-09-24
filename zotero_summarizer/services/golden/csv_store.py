"""Serialized, atomic read-modify-write for the shared golden dataset."""
from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator


def read_snapshot(path: Path) -> tuple[list[dict[str, str]], str]:
    """Return rows and SHA-256 from the same read, before any caller's writes."""
    raw = path.read_bytes()
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"), newline="")))
    return rows, hashlib.sha256(raw).hexdigest()


@contextmanager
def edit_csv(
    path: Path, *, create_fields: list[str] | None = None,
) -> Iterator[tuple[list[str], list[dict[str, Any]]]]:
    """Edit one snapshot under a per-file, cross-process SQLite lock.

    Missing files require explicit creation fields. Exceptions leave the CSV
    unchanged; a no-op does not rewrite it. The lock sidecar survives process
    exit, but SQLite releases its lock automatically.
    """
    from zotero_summarizer.services._common import atomic_write

    path = path.resolve()
    with closing(sqlite3.connect(path.with_name(path.name + ".lock.sqlite"), timeout=10)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        exists = path.exists()
        if exists:
            with path.open(newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source, strict=True)
                rows = list(reader)
                fields = list(reader.fieldnames or [])
        elif create_fields is not None:
            fields, rows = list(create_fields), []
        else:
            raise FileNotFoundError(f"golden CSV not found at {path}; run `goldenset export` first")
        if "item_key" not in fields:
            raise ValueError(f"golden CSV {path} has no item_key column — refusing to overwrite it")
        if len(set(fields)) != len(fields) or any(None in row or None in row.values() for row in rows):
            raise ValueError(f"golden CSV {path} has duplicate columns or malformed rows")
        # ponytail: whole-file snapshots; use SQLite rows if label volume outgrows memory.
        original = (fields.copy(), [row.copy() for row in rows])
        yield fields, rows
        if exists and (fields, rows) == original:
            return

        def write(target: Path) -> None:
            with target.open("w", newline="", encoding="utf-8") as dest:
                writer = csv.DictWriter(dest, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)

        atomic_write(path, write)
