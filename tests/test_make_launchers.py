"""Make shortcuts must preserve the local-only and owner-only gateway boundaries."""
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest


DEFAULT_MAKE_TIMEOUT_SECONDS = 10
MAX_MAKE_TIMEOUT_SECONDS = 60
ORIGIN = "https://mac.example-tailnet.ts.net"
STATUS = {"BackendState": "Running", "CertDomains": ["mac.example-tailnet.ts.net"],
          "Self": {"DNSName": "mac.example-tailnet.ts.net.", "UserID": 7},
          "User": {"7": {"LoginName": "owner@example.com"}}}


@pytest.fixture(params=[False, True], ids=["path-binary", "venv-binary"])
def launcher_project(tmp_path, request):
    root = Path(__file__).resolve().parents[1]
    for name in ("Makefile", "tools/serve-iphone.sh"):
        copy = tmp_path / name
        copy.parent.mkdir(exist_ok=True)
        copy.write_bytes((root / name).read_bytes())
        copy.chmod(0o755)
    built = tmp_path / "frontend" / "dist" / "index.html"
    built.parent.mkdir(parents=True)
    built.write_text("")
    calls, status = tmp_path / "invocations", tmp_path / "status.json"
    tools = tmp_path / "bin"
    tools.mkdir()
    stubs = [tools / "zotero-summarizer", tools / "tailscale"]
    if request.param:
        stubs.append(tmp_path / ".venv" / "bin" / "zotero-summarizer")
        stubs[-1].parent.mkdir(parents=True)
    for stub in stubs:
        reply = f'[ "$1" = status ] && cat {shlex.quote(str(status))}\n' if stub.name == "tailscale" else ""
        stub.write_text(f'#!/bin/sh\necho "{stub.name} $*" >> {shlex.quote(str(calls))}\n{reply}exit 0\n')
        stub.chmod(0o755)
    return tmp_path, tools, calls, status


def _make(root, tools, target):
    timeout = float(os.environ.get("ZS_TEST_MAKE_TIMEOUT_SECONDS", DEFAULT_MAKE_TIMEOUT_SECONDS))
    if not 0 < timeout <= MAX_MAKE_TIMEOUT_SECONDS:
        raise ValueError("invalid bounded Make-test timeout")
    env = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"]}
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKEFILES"):
        env.pop(name, None)
    return subprocess.run(["make", "--no-print-directory", target], cwd=root, env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_make_run_serves_loopback_only(launcher_project):
    root, tools, calls, _status = launcher_project
    result = _make(root, tools, "run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text() == "zotero-summarizer serve --host 127.0.0.1 --port 8000\n"


def test_make_iphone_admits_only_the_detected_tailnet_owner(launcher_project):
    root, tools, calls, status = launcher_project
    status.write_text(json.dumps(STATUS))
    result = _make(root, tools, "iphone")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Open {ORIGIN} " in result.stdout
    assert calls.read_text().splitlines() == [
        "tailscale status --json",
        "tailscale serve --bg --https=443 http://127.0.0.1:8000",
        f"zotero-summarizer serve-mobile --origin {ORIGIN} --owner-login owner@example.com"
        " --trusted-loopback-port 8000",
        "tailscale serve --https=443 off",
    ]


@pytest.mark.parametrize("change", [{"BackendState": "NeedsLogin"}, {"CertDomains": None}],
                         ids=["signed-out", "no-https-certificates"])
def test_make_iphone_refuses_before_any_exposure(launcher_project, change):
    root, tools, calls, status = launcher_project
    status.write_text(json.dumps({**STATUS, **change}))
    result = _make(root, tools, "iphone")
    assert result.returncode != 0
    assert "Tailscale" in result.stderr
    assert calls.read_text() == "tailscale status --json\n"
