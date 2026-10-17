"""Make shortcuts must preserve the supported local-only deployment boundary."""
import os
from pathlib import Path
import shlex
import subprocess

import pytest


DEFAULT_MAKE_TIMEOUT_SECONDS = 10
MAX_MAKE_TIMEOUT_SECONDS = 60


@pytest.fixture(params=[False, True], ids=["path-binary", "venv-binary"])
def launcher_project(tmp_path, request):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "Makefile").write_bytes((root / "Makefile").read_bytes())
    calls = tmp_path / "invocations"
    script = f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> {shlex.quote(str(calls))}\n"
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("ipconfig", "zotero-summarizer"):
        executable = tools / name
        executable.write_text(script)
        executable.chmod(0o755)
    if request.param:
        executable = tmp_path / ".venv" / "bin" / "zotero-summarizer"
        executable.parent.mkdir(parents=True)
        executable.write_text(script)
        executable.chmod(0o755)
    return tmp_path, tools, calls


@pytest.mark.parametrize("target", ["iphone", "api"])
def test_make_launchers_respect_deployment_boundary(launcher_project, target):
    root, tools, calls = launcher_project
    timeout = float(os.environ.get("ZS_TEST_MAKE_TIMEOUT_SECONDS", DEFAULT_MAKE_TIMEOUT_SECONDS))
    if not 0 < timeout <= MAX_MAKE_TIMEOUT_SECONDS:
        raise ValueError("invalid bounded Make-test timeout")
    env = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"]}
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKEFILES"):
        env.pop(name, None)
    result = subprocess.run(["make", "--no-print-directory", target], cwd=root, env=env,
                            capture_output=True, text=True, timeout=timeout)
    if target == "iphone":
        assert not calls.exists(), result.stdout + result.stderr
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith(
            "make iphone is unavailable: authenticated HTTPS mobile access is not configured.\n"
            "For this Mac, run make api and open http://127.0.0.1:8000.\n"
            "See docs/local-deployment-boundary.md for the mobile deployment requirements.\n"
        )
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.read_text() == "serve\n--host\n127.0.0.1\n--port\n8000\n"
