from zotero_summarizer.services.library import deep_review


def test_aggregate_status_uses_latest_finished_attempt_for_error():
    with deep_review._LOCK:
        deep_review._JOBS.clear()
        deep_review._JOBS.update({
            "OLD": {"status": "error", "started_at": "2026-01-01T00:00:00Z", "error": "old failure"},
            "NEW": {"status": "ready", "started_at": "2026-01-02T00:00:00Z", "error": None},
        })

    status = deep_review.status()

    assert status["status"] == "ready"
    assert status["error"] is None
    assert status["completed"] == 2

    with deep_review._LOCK:
        deep_review._JOBS["RUNNING"] = {"status": "running", "started_at": "2026-01-03T00:00:00Z"}
    status = deep_review.status()
    assert status["status"] == "running"
    assert status["error"] is None
