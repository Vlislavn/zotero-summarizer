"""Dependency reuse must not stage a machine-local absolute symbolic link."""
import os
import subprocess
from pathlib import Path

import pytest

DEFAULT_GIT_TIMEOUT_SECONDS = 10
MAX_GIT_TIMEOUT_SECONDS = 60


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_frontend_dependencies_are_ignored_for_both_filesystem_types(tmp_path, kind):
    timeout = float(os.environ.get("ZS_TEST_GIT_TIMEOUT_SECONDS", DEFAULT_GIT_TIMEOUT_SECONDS))
    if not 0 < timeout <= MAX_GIT_TIMEOUT_SECONDS:
        raise ValueError("invalid bounded Git-test timeout")
    root = tmp_path / "repo"
    root.mkdir()
    empty = tmp_path / "no-global-excludes"
    empty.write_text("")
    command = ["git", "-c", "core.excludesFile=" + str(empty), "-c", "init.templateDir=", "-C", str(root)]
    subprocess.run([*command, "init", "-q"], check=True, capture_output=True, timeout=timeout)
    frontend = root / "frontend"
    frontend.mkdir()
    source = Path(__file__).resolve().parents[1] / "frontend" / ".gitignore"
    (frontend / ".gitignore").write_bytes(source.read_bytes())
    node_modules = frontend / "node_modules"
    if kind == "directory":
        node_modules.mkdir()
        candidate = "frontend/node_modules/probe.js"
        (node_modules / "probe.js").write_text("dependency fixture")
    else:
        target = tmp_path / "shared-dependencies"
        target.mkdir()
        node_modules.symlink_to(target, target_is_directory=True)
        candidate = "frontend/node_modules"
    result = subprocess.run([*command, "check-ignore", "-v", "--no-index", candidate],
                            capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "frontend/.gitignore:" in result.stdout
