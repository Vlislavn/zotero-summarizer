"""Application logging is owned by one test, including its file resources."""
from contextlib import ExitStack, contextmanager, nullcontext
import logging
from pathlib import Path

import pytest

from conftest import _isolate_app_and_network
from zotero_summarizer.services import _common


@contextmanager
def _test_boundary(tmp_path):
    with pytest.MonkeyPatch.context() as patch, ExitStack() as teardown:
        boundary = _isolate_app_and_network.__wrapped__(patch, tmp_path)
        # Also exercise the pre-fix, setup-only fixture for the RED check.
        if boundary is not None:
            next(boundary)
            teardown.callback(next, boundary, None)
        yield


@pytest.mark.parametrize("level,propagate,disabled", [
    (logging.WARNING, True, False),
    (logging.ERROR, False, False),
    (logging.WARNING, True, True),
])
@pytest.mark.parametrize("test_fails", [False, True])
def test_logging_boundary_restores_state_and_resources(
    tmp_path, monkeypatch, caplog, level, propagate, disabled, test_fails,
):
    logger = _common.LOGGER
    monkeypatch.setenv("APP_LOG_LEVEL", "INFO")
    monkeypatch.setenv("APP_LOG_FILE", str(tmp_path / "app.log"))
    monkeypatch.setattr(logger, "propagate", propagate)
    monkeypatch.setattr(logger, "disabled", disabled)
    with ExitStack() as cleanup:
        cleanup.callback(logger.setLevel, logger.level)
        originals = [logging.FileHandler(tmp_path / f"original-{i}.log") for i in range(2)]
        for handler in originals:
            cleanup.callback(handler.close)
        monkeypatch.setattr(logger, "handlers", originals.copy())
        logger.setLevel(level)
        enabled = logger.isEnabledFor(logging.WARNING)
        expected_error = pytest.raises(RuntimeError, match="synthetic failure") if test_fails else nullcontext()
        with expected_error, _test_boundary(tmp_path):
            logger.disabled = False
            _common.setup_logging()
            assert logger.level == logging.INFO
            assert logger.propagate is False
            owned = [handler for handler in logger.handlers if handler not in originals]
            assert len(owned) == 1
            file_handler = owned[0]
            cleanup.callback(file_handler.close)
            stream = file_handler.stream
            assert file_handler.name == "zotero_file"
            assert Path(file_handler.baseFilename) == tmp_path / "app.log"
            _common.setup_logging()
            assert logger.handlers == [*originals, file_handler]
            logger.info("initializer synthetic record")
            assert "initializer synthetic record" in (tmp_path / "app.log").read_text()
            assert caplog.records == []
            logger.removeHandler(originals[0])
            logger.handlers.reverse()
            logger.setLevel(logging.DEBUG if level == logging.ERROR else logging.CRITICAL)
            assert logger.isEnabledFor(logging.WARNING) == (level == logging.ERROR)
            logger.propagate = not propagate
            logger.disabled = not disabled
            if test_fails:
                raise RuntimeError("synthetic failure")

        assert (logger.level, logger.propagate, logger.disabled) == (level, propagate, disabled)
        assert logger.handlers == originals
        assert all(not handler._closed and not handler.stream.closed for handler in originals)
        assert file_handler._closed and stream.closed and file_handler.stream is None
        assert logger.isEnabledFor(logging.WARNING) == enabled
        logger.warning("later synthetic warning")
        expected = [(logger.name, logging.WARNING, "later synthetic warning")] if enabled and propagate else []
        assert caplog.record_tuples == expected
