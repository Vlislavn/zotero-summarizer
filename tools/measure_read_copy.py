"""Measure the Zotero read copy (S0 / Q8): copy time, copy size, sync-to-copy delay.

Run by the owner with Zotero Desktop OPEN (the live file is only read through SQLite's
backup API, never written)::

    .venv/bin/python tools/measure_read_copy.py --zotero-dir ~/Zotero --timeout 300

1. copies the library once into a scratch file and prints seconds + size;
2. asks you to add/sync a NEW item in Zotero, then polls (re-copying every --poll s)
   until an item id above the baseline appears, and prints the delay from your Enter.

In the app the copy is refreshed on every source change (max staleness 0 s while the source
is readable; ``refresh_interval_seconds`` is an opt-in throttle). Cost per refresh = the copy
time printed in step 1.
"""
from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from zotero_summarizer.integrations._zotero_read_copy import DEFAULT_REFRESH_INTERVAL_SECONDS
from zotero_summarizer.integrations.zotero_read import ZoteroReader


def _max_item_id(reader: ZoteroReader) -> int:
    return reader._execute_read(lambda conn: int(conn.execute("SELECT COALESCE(MAX(itemID), 0) FROM items").fetchone()[0]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--zotero-dir", required=True, type=Path, help="Zotero data dir (contains zotero.sqlite)")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for the new item")
    parser.add_argument("--poll", type=float, default=2.0, help="seconds between re-copies while waiting")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="zs-read-copy-") as scratch:
        copy_path = Path(scratch) / "zotero.sqlite"
        reader = ZoteroReader(args.zotero_dir, copy_path, refresh_interval_seconds=0.0)
        started = time.perf_counter()
        baseline = _max_item_id(reader)  # first read triggers the copy
        print(f"copy time: {time.perf_counter() - started:.2f} s | copy size: {copy_path.stat().st_size / 1e6:.1f} MB"
              f" | stale_since: {reader.stale_since}")
        print(f"app refresh interval (max staleness while readable): {DEFAULT_REFRESH_INTERVAL_SECONDS:.0f} s"
              " (copy on every source change; each refresh costs the copy time above)")
        input(f"Baseline max itemID = {baseline}. Add/sync a NEW item in Zotero, then press Enter to start timing... ")
        started = time.perf_counter()
        while time.perf_counter() - started < args.timeout:
            if _max_item_id(reader) > baseline:
                print(f"new item visible in the copy after {time.perf_counter() - started:.1f} s")
                return 0
            time.sleep(args.poll)
        print(f"no new item after {args.timeout:.0f} s (stale_since: {reader.stale_since})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
