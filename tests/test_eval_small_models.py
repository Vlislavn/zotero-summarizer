"""The eval_small_models tool's latency probe — cold/warm separation discipline.

Controlled-latency rule: a wallclock decision must use warm steady-state, never a
load-contaminated cold number (the fix that caught the "0.8b 4x faster" error).
"""
from __future__ import annotations

import time

import pytest

from tools.eval_small_models import measure_latency


def test_measure_latency_separates_cold_from_warm() -> None:
    class _SlowFirst:
        def __init__(self):
            self.calls = 0

        def prompt(self, _prompt):
            self.calls += 1
            if self.calls == 1:
                time.sleep(0.05)  # simulate the cold model load on the first call
            return "ok"

    llm = _SlowFirst()
    res = measure_latency(llm, warmups=1, samples=2)
    assert llm.calls == 3  # 1 cold + 2 warm samples
    assert res["cold_secs"] >= 0.05
    assert res["cold_start_overhead_secs"] > 0.02  # cold clearly slower than warm steady-state
    with pytest.raises(ValueError):
        measure_latency(llm, warmups=0)
