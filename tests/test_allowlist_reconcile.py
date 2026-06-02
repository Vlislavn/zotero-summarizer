"""Tests for the allowlist staleness reconciler (``check_allowlists.py``).

``tools/precommit/`` is not importable, so the module is loaded by path. The
pure key-set logic is unit-tested; one integration test asserts the committed
allowlists hold no stale grandfather (the rot-catcher, run in CI).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECON_PATH = _REPO_ROOT / "tools" / "precommit" / "check_allowlists.py"
_spec = importlib.util.spec_from_file_location("check_allowlists", _RECON_PATH)
assert _spec is not None and _spec.loader is not None
recon = importlib.util.module_from_spec(_spec)
sys.modules["check_allowlists"] = recon
_spec.loader.exec_module(recon)


# --- pure key logic ---


def test_parse_keys_strips_comments_and_blanks() -> None:
    text = (
        "# header\n"
        "\n"
        "pkg/a.py:foo   # test-only\n"
        "  pkg/b.py:Bar  \n"
        "# pkg/c.py:skipped\n"
    )
    assert recon.parse_keys(text) == {"pkg/a.py:foo", "pkg/b.py:Bar"}


def test_live_keys_take_first_token_per_line() -> None:
    dump = (
        "pkg/a.py:foo  # unused function (pkg/a.py:1)\n"
        "pkg/b.py:Bar\n"
        "\n"
        "pkg/c.py:9:todo-stub\n"
    )
    assert recon.live_keys(dump) == {"pkg/a.py:foo", "pkg/b.py:Bar", "pkg/c.py:9:todo-stub"}


def test_find_stale_is_committed_minus_live() -> None:
    committed = {"a:x", "b:y", "c:z"}
    live = {"a:x", "c:z"}
    assert recon.find_stale(committed, live) == {"b:y"}


def test_find_stale_empty_when_committed_subset_of_live() -> None:
    assert recon.find_stale({"a:x"}, {"a:x", "b:y"}) == set()


# --- integration: the committed allowlists must be stale-free ---


def test_committed_allowlists_have_no_stale_grandfathers() -> None:
    # Guards against allowlist rot (a grandfather whose target was deleted/fixed)
    # — exactly the failure a reverted/over-broad whitelist would introduce.
    pytest.importorskip("vulture")  # reconcile regenerates the vulture live set
    result = subprocess.run(
        [sys.executable, str(_RECON_PATH), "reconcile"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stale grandfathers found:\n{result.stderr}"
