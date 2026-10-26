"""Owned -I child path controls only; no SDK, product entry, fuse or account."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_telegram_cli_lifetime import OBSERVATION_FACTOR, WALL_SECONDS, owned_child


IMPORT_CHILD = r'''
import json, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], *sys.argv[4:]]
import owned_path_probe
print(json.dumps({'path': str(Path(owned_path_probe.__file__).resolve()),
                  'value': owned_path_probe.VALUE, 'isolated': sys.flags.isolated,
                  'no_bytecode': sys.dont_write_bytecode}), flush=True)
'''


@pytest.mark.parametrize("explicit", [False, True], ids=["no_inheritance", "explicit_root"])
def test_owned_child_import_root_is_explicit_not_parent_pythonpath(tmp_path, monkeypatch, explicit):
    selected, inherited = tmp_path / "selected", tmp_path / "inherited"
    selected.mkdir()
    inherited.mkdir()
    (selected / "owned_path_probe.py").write_text("VALUE = 'selected'\n")
    (inherited / "owned_path_probe.py").write_text("VALUE = 'inherited'\n")
    monkeypatch.setenv("PYTHONPATH", str(inherited if explicit else selected))
    roots = (selected.resolve(),) if explicit else ()
    with owned_child(tmp_path, "import_probe", script=IMPORT_CHILD, import_roots=roots) as (process, _):
        output, error = process.communicate(timeout=WALL_SECONDS * OBSERVATION_FACTOR)
        if explicit:
            assert process.returncode == 0 and error == b""
            assert json.loads(output) == {
                "path": str((selected / "owned_path_probe.py").resolve()), "value": "selected",
                "isolated": 1, "no_bytecode": True,
            }
        else:
            assert process.returncode != 0 and output == b""
            assert b"ModuleNotFoundError" in error and b"owned_path_probe" in error
    assert not list(selected.glob("__pycache__/*"))


@pytest.mark.parametrize("kind", ["relative", "missing", "symlink"])
def test_owned_child_invalid_import_root_refused_before_spawn(tmp_path, monkeypatch, kind):
    from test_telegram_cli_lifetime import subprocess

    selected = tmp_path / "selected"
    selected.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(selected, target_is_directory=True)
    root = {"relative": Path("relative"), "missing": tmp_path / "missing", "symlink": alias}[kind]

    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid import root reached child launch")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(AssertionError):
        with owned_child(tmp_path, "import_probe", script=IMPORT_CHILD, import_roots=(root,)):
            pytest.fail("invalid import root admitted")
