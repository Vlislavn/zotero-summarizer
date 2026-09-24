from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

from zotero_summarizer.services.faithbench._report import _append_headline_once


def _append_when_released(path: str, headline: dict, ready) -> None:
    ready.wait()
    _append_headline_once(Path(path), headline)


def test_master_headline_append_is_idempotent_across_processes(tmp_path):
    path = tmp_path / "faithbench-runs.jsonl"
    path.write_text('{"run_id":"existing"}\n', encoding="utf-8")
    entries = [
        {"run_id": "same", "value": 1},
        {"run_id": "same", "value": 2},
        {"run_id": "other-a", "value": 3},
        {"run_id": "other-b", "value": 4},
    ]
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    workers = [context.Process(target=_append_when_released, args=(str(path), entry, ready))
               for entry in entries]
    for worker in workers:
        worker.start()
    ready.set()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["run_id"] for row in rows].count("same") == 1
    assert {row["run_id"] for row in rows} == {"existing", "same", "other-a", "other-b"}
