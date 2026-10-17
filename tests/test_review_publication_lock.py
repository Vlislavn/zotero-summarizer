"""Two fresh processes contend on the actual cache writer/approval lock."""
from multiprocessing import get_context
from pathlib import Path

import pytest

PROCESS_JOIN_SECONDS = 10  # test-owned safety net; no product timeout


def _attempt_cache_write(path, operation, output):
    from filelock import FileLock, Timeout
    from zotero_summarizer.services.library import _review_cache

    _review_cache._cache_path = lambda: Path(path)
    name = path + ".lock"
    _review_cache._PUBLICATION_LOCKS[name] = FileLock(name, timeout=0)
    try:
        if operation == "one":
            _review_cache._write_one("CHILD", {"fixture": True})
        elif operation == "clear":
            _review_cache._write_all({})
        else:
            with _review_cache.review_publication_lock():
                _review_cache.publication_snapshot("PARENT")
    except Timeout:
        output.put("blocked")
    else:
        output.put("written")


def _process_result(path, operation):
    context = get_context("spawn")
    output = context.Queue()
    child = context.Process(target=_attempt_cache_write, args=(str(path), operation, output))
    try:
        child.start()
        child.join(PROCESS_JOIN_SECONDS)
        assert not child.is_alive() and child.exitcode == 0
        return output.get(timeout=PROCESS_JOIN_SECONDS)
    finally:
        if child.is_alive():
            child.terminate()
            child.join(PROCESS_JOIN_SECONDS)
        output.close()
        output.join_thread()


@pytest.mark.parametrize("operation", ["one", "clear", "approval_snapshot"])
def test_cache_publication_and_approval_share_a_cross_process_lock(tmp_path, monkeypatch, operation):
    from zotero_summarizer.services.library import _review_cache

    path = tmp_path / "reviews.json"
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: path)
    _review_cache._write_one("PARENT", {"fixture": "original"})
    before = path.read_bytes()
    with _review_cache.review_publication_lock():
        assert _process_result(path, operation) == "blocked"
        assert path.read_bytes() == before
    assert _process_result(path, operation) == "written"
    if operation == "one":
        assert set(_review_cache._read_all(readonly=True)) == {"PARENT", "CHILD"}
    elif operation == "clear":
        assert _review_cache._read_all(readonly=True) == {}
    else:
        assert path.read_bytes() == before
