"""Provider failures must not publish partial predictions or success reports."""

from concurrent.futures import Future
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from tests.test_classifier_evaluation_truth import _project, _run_report
from zotero_summarizer.cli import main
from zotero_summarizer.services import _adapters
from zotero_summarizer.services.golden.csv_store import edit_csv
from zotero_summarizer.services.model import llm_classifier


@pytest.mark.parametrize("failure", ["all", "after_success", "priority", "confidence"])
def test_cli_failure_preserves_predictions_and_reports(tmp_path, monkeypatch, failure):
    settings = _project(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_CLASSIFIER_API_KEY", "test-only")
    log = settings.data_dir / "classifier-runs.jsonl"
    log.write_text('{"run_id":"prior"}\n')
    report = settings.data_dir / "reports" / "prior.md"
    report.parent.mkdir()
    report.write_text("Prior successful benchmark\n")
    paths = [settings.golden_csv_path, log, report]
    before = [path.read_bytes() for path in paths]
    provider_error = RuntimeError("test provider unavailable")
    calls = []

    class Client:
        def pydantic_prompt(self, *, prompt, pydantic_model):
            calls.append(prompt)
            if failure == "all" or (failure == "after_success" and len(calls) > 1):
                raise provider_error
            return pydantic_model(
                priority="nonsense" if failure == "priority" else "must_read",
                confidence=2 if failure == "confidence" else .9, rationale="Test",
            )

    monkeypatch.setattr(_adapters, "build_llm", lambda *args, **kwargs: Client())
    publish = Mock(wraps=llm_classifier.write_predictions_to_csv)
    monkeypatch.setattr(llm_classifier, "write_predictions_to_csv", publish)
    with pytest.raises((RuntimeError, ValidationError)) as error:
        main(["goldenset", "classify-llm", "--project-root", str(tmp_path),
              "--classifier-name", "test", "--limit", "2", "--workers", "1"])
    if failure in {"all", "after_success"}:
        assert error.value is provider_error
    if failure == "after_success":
        assert len(calls) == 2
    publish.assert_not_called()
    assert [path.read_bytes() for path in paths] == before
    assert list(report.parent.iterdir()) == [report]


@pytest.mark.parametrize("error", [RuntimeError("worker failed"), KeyboardInterrupt()])
@pytest.mark.parametrize("phase", ["completion", "submission"])
def test_failure_cancels_queued_futures_and_preserves_original_exception(monkeypatch, error, phase):
    futures = []

    class Executor:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def submit(self, *args):
            if phase == "submission" and futures:
                raise error
            future = Future()
            if phase == "completion" and not futures:
                future.set_exception(error)
            futures.append(future)
            return future

    monkeypatch.setattr(llm_classifier, "ThreadPoolExecutor", Executor)
    with pytest.raises(type(error)) as caught:
        llm_classifier.classify_papers_with_llm(
            [{"item_key": str(i), "title": "Title", "abstract": "Abstract"} for i in range(4)],
            Mock(), research_goals=[], workers=2,
        )
    assert caught.value is error
    pending = futures[1:] if phase == "completion" else futures
    assert pending and all(future.cancelled() for future in pending)


def test_cli_reports_missing_metadata_as_skipped_not_provider_failure(tmp_path, monkeypatch):
    settings = _project(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_CLASSIFIER_API_KEY", "test-only")
    with edit_csv(settings.golden_csv_path) as (_, rows):
        rows[0]["abstract"] = ""
    client = Mock()
    client.pydantic_prompt.return_value = llm_classifier._LLMVerdict(
        priority="dont_read", confidence=.9,
    )
    monkeypatch.setattr(_adapters, "build_llm", lambda *args, **kwargs: client)

    assert main(["goldenset", "classify-llm", "--project-root", str(tmp_path),
                 "--classifier-name", "test", "--limit", "2", "--workers", "1"]) == 0

    report = _run_report(settings)
    assert report["rows_processed"] == 2
    assert report["rows_skipped"] == report["rows_with_priority"] == 1
    assert "rows_failed" not in report
    assert report["csv_updated_rows"] == report["cv"]["metrics_vs_gold"]["total"] == 1
    client.pydantic_prompt.assert_called_once()
