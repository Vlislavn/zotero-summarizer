"""Exercise the real autouse boundary across ordered pytest subprocess scenarios."""
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def test_app_logger_state_and_owned_handlers_do_not_escape_a_test(pytester):
    result = _run_logger_scenario(pytester, '''
import logging
from zotero_summarizer.services._common import LOGGER, setup_logging

LOGGER.setLevel(logging.WARNING)
LOGGER.propagate = True
LOGGER.disabled = False

class ProbeHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.closed_by_owner = False
        self.records = []
    def emit(self, record):
        self.records.append(record)
    def close(self):
        self.closed_by_owner = True
        super().close()

existing = ProbeHandler()
LOGGER.addHandler(existing)
initial = (LOGGER.level, LOGGER.propagate, LOGGER.disabled, LOGGER.handlers[:])
added = None

def test_mutator(monkeypatch, tmp_path):
    global added
    monkeypatch.setenv("APP_LOG_FILE", str(tmp_path / "server.log"))
    setup_logging()
    LOGGER.setLevel(logging.DEBUG)
    assert LOGGER.isEnabledFor(logging.DEBUG)
    LOGGER.propagate = False
    LOGGER.disabled = True
    added = ProbeHandler()
    LOGGER.addHandler(added)
    LOGGER.removeHandler(existing)
    assert not added.closed_by_owner and not existing.closed_by_owner

def test_victim_observes_restored_state(caplog):
    level, propagate, disabled, handlers = initial
    assert LOGGER.level == level
    assert LOGGER.propagate == propagate and LOGGER.disabled == disabled
    assert LOGGER.handlers == handlers
    assert not existing.closed_by_owner
    assert added.closed_by_owner
    assert not LOGGER.isEnabledFor(logging.DEBUG)
    with caplog.at_level(logging.WARNING, logger=LOGGER.name):
        LOGGER.warning("Owned fixture observation", extra={"fixture_owner_probe": "restored"})
    assert any(getattr(row, "fixture_owner_probe", None) == "restored" for row in caplog.records)
''')
    result.assert_outcomes(passed=2)


def _run_logger_scenario(pytester, source):
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makepyfile(test_logger_lifecycle=source)
    return pytester.runpytest_subprocess("-q", "-p", "no:cacheprovider", "-o", "addopts=")


@pytest.mark.parametrize("owner_name,attached_before_test", [
    ("", True), ("fixture_shared_owner", True), ("", False),
])
def test_handlers_owned_by_other_loggers_stay_open(pytester, owner_name, attached_before_test):
    result = _run_logger_scenario(pytester, f'''
import logging
from pathlib import Path
from zotero_summarizer.services._common import LOGGER

owner = logging.getLogger({owner_name!r})
owner.setLevel(logging.WARNING)
path = Path(__file__).with_name("shared.log")
shared = logging.FileHandler(path, mode="w")
if {attached_before_test!r}:
    owner.addHandler(shared)
baseline = LOGGER.handlers[:]

def test_attach_shared_handler():
    owner.addHandler(shared)
    LOGGER.addHandler(shared)

def test_owner_can_still_write():
    assert LOGGER.handlers == baseline
    assert shared in owner.handlers and not shared._closed
    owner.warning("shared handler still works")
    shared.flush()
    assert "shared handler still works" in path.read_text()
''')
    result.assert_outcomes(passed=2)


def test_close_failure_restores_state_and_closes_other_handlers(pytester):
    result = _run_logger_scenario(pytester, '''
import logging
from pathlib import Path
from zotero_summarizer.services._common import LOGGER

class FailingHandler(logging.Handler):
    def close(self):
        if not getattr(self, "close_attempted", False):
            self.close_attempted = True
            raise RuntimeError("close sentinel")
        super().close()

LOGGER.setLevel(logging.WARNING)
LOGGER.propagate = True
LOGGER.disabled = False
existing = logging.NullHandler()
LOGGER.addHandler(existing)
baseline = (LOGGER.level, LOGGER.propagate, LOGGER.disabled, LOGGER.handlers[:])
failing = None
earlier = None
later = None

def test_mutate_and_add_handlers():
    global failing, earlier, later
    failing = FailingHandler()
    earlier = logging.FileHandler(Path(__file__).with_name("earlier.log"), mode="w")
    later = logging.FileHandler(Path(__file__).with_name("later.log"), mode="w")
    LOGGER.addHandler(earlier)
    LOGGER.addHandler(failing)
    LOGGER.addHandler(later)
    LOGGER.removeHandler(existing)
    LOGGER.setLevel(logging.DEBUG)
    assert LOGGER.isEnabledFor(logging.DEBUG)
    LOGGER.propagate = False
    LOGGER.disabled = True

def test_baseline_survives_close_error(caplog):
    assert (LOGGER.level, LOGGER.propagate, LOGGER.disabled, LOGGER.handlers) == baseline
    assert not existing._closed
    assert failing.close_attempted
    assert earlier._closed and earlier.stream is None
    assert later._closed and later.stream is None
    assert not LOGGER.isEnabledFor(logging.DEBUG)
    LOGGER.warning("capture after close error")
    assert any(row.message == "capture after close error" for row in caplog.records)
''')
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*RuntimeError: close sentinel*"])


def test_lifecycle_regression_ignores_inherited_log_path(pytester, monkeypatch):
    outside = pytester.path / "outside-test-home" / "sentinel.log"
    monkeypatch.setenv("APP_LOG_FILE", str(outside))
    test_app_logger_state_and_owned_handlers_do_not_escape_a_test(pytester)
    assert not outside.exists()


def test_lifecycle_setup_failure_does_not_mutate_outer_logger(pytester, monkeypatch):
    from zotero_summarizer.services._common import LOGGER

    baseline = (LOGGER.level, LOGGER.propagate, LOGGER.disabled, LOGGER.handlers[:])

    def fail_setup(_source):
        raise OSError("pytester setup sentinel")

    monkeypatch.setattr(pytester, "makeconftest", fail_setup)
    with pytest.raises(OSError, match="pytester setup sentinel"):
        test_app_logger_state_and_owned_handlers_do_not_escape_a_test(pytester)
    assert (LOGGER.level, LOGGER.propagate, LOGGER.disabled, LOGGER.handlers) == baseline
