"""Exercise the real autouse boundary across an ordered, nested pytest scenario."""
import logging
from pathlib import Path

pytest_plugins = ["pytester"]


def test_app_logger_state_and_owned_handlers_do_not_escape_a_test(pytester):
    from zotero_summarizer.services._common import LOGGER

    baseline = (LOGGER.level, LOGGER.propagate, LOGGER.disabled, LOGGER.handlers[:])
    LOGGER.setLevel(logging.WARNING)
    LOGGER.propagate = True
    LOGGER.disabled = False
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makepyfile(test_logger_lifecycle='''
import logging
from zotero_summarizer.services._common import LOGGER, setup_logging

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

def test_mutator():
    global added
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
    try:
        result = pytester.runpytest_inprocess("-q", "-p", "no:cacheprovider", "-o", "addopts=")
        result.assert_outcomes(passed=2)
    finally:
        level, propagate, disabled, handlers = baseline
        for handler in LOGGER.handlers[:]:
            if handler not in handlers:
                LOGGER.removeHandler(handler)
                handler.close()
        LOGGER.handlers[:] = handlers
        LOGGER.setLevel(level)
        LOGGER.propagate = propagate
        LOGGER.disabled = disabled
