"""Stock-Uvicorn versus owned-gateway interrupted-exit observations, not TLS proof."""
import json
import signal
from pathlib import Path

import pytest

from _gateway_process import SAFETY_SECONDS, owned_server


@pytest.mark.parametrize("mode", ["local", "mobile"])
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_stock_and_gateway_interruptions_are_non_successful_and_owned(tmp_path, mode, signum):
    with owned_server(tmp_path, mode) as (child, info):
        child.send_signal(signum)
        child.wait(timeout=SAFETY_SECONDS)
        result = {"mode": mode, "signal": int(signum), "returncode": child.returncode}
        if mode == "mobile":
            result["socket_absent_before_fixture_cleanup"] = not Path(info["address"]).exists()
            assert result["socket_absent_before_fixture_cleanup"]
        (tmp_path / "interrupted-exit.json").write_text(json.dumps(result, indent=2))
        assert child.returncode != 0
