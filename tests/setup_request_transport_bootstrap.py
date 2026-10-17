"""Run only the 17 actual-SDK cases in a fresh, guarded interpreter.

Parent execution only; see tests/README.md. This is not an OS sandbox or a
production admission mechanism. No broader selectors or pytest args are accepted.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import inspect
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "tests/test_setup_request_transport.py"
PREREQUISITE = "ZS_TEST_SETUP_NATIVE_SDK"
TIMEOUT_ENV = "ZS_TEST_SETUP_NATIVE_SDK_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 120.0


def _timeout_seconds(raw):
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    message = (f"{TIMEOUT_ENV} must be finite seconds > 0 and "
               f"<= {MAX_TIMEOUT_SECONDS:g}")
    try:
        seconds = float(raw)
    except ValueError as exc:
        raise ValueError(message) from exc
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(message)
    return seconds


def _environment(sandbox):
    paths = {name: sandbox / name for name in ("home", "cache", "config", "app", "tmp")}
    for path in paths.values():
        path.mkdir()
    return {
        "PATH": os.defpath,
        "HOME": str(paths["home"]),
        "TMPDIR": str(paths["tmp"]),
        "XDG_CACHE_HOME": str(paths["cache"]),
        "XDG_CONFIG_HOME": str(paths["config"]),
        "XDG_DATA_HOME": str(sandbox / "data"),
        "HF_HOME": str(paths["cache"] / "huggingface"),
        "TORCH_HOME": str(paths["cache"] / "torch"),
        "ZOTERO_SUMMARIZER_HOME": str(paths["app"]),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "ANONYMIZED_TELEMETRY": "False",
        "LITELLM_MODE": "PRODUCTION",
        "AWS_EC2_METADATA_DISABLED": "true",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "NO_PROXY": "*",
        "no_proxy": "*",
        PREREQUISITE: "1",
    }


def _guards(sandbox):
    import socket

    def refuse(*_args, **_kwargs):
        raise AssertionError("Actual-SDK proof forbids dotenv discovery/loading")

    # Import python-dotenv itself only; its public import does not discover files.
    import dotenv
    import dotenv.main

    for module in (dotenv, dotenv.main):
        for name in ("load_dotenv", "find_dotenv", "dotenv_values"):
            setattr(module, name, refuse)
    dotenv.main.DotEnv.__init__ = refuse
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def audit(event, args):
        if event == "socket.__new__" and args[1] in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Actual-SDK proof forbids TCP/UDP sockets")
        if event == "socket.getaddrinfo":
            raise AssertionError("Actual-SDK proof forbids DNS")
        if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork"}:
            raise AssertionError("Actual-SDK child cannot launch another process")
        if event != "open" or isinstance(args[0], int):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        if path.name == ".env" or path.name.startswith(".env."):
            raise AssertionError("Actual-SDK proof forbids dotenv reads")
        if path.suffix.lower() in {".gguf", ".ggml", ".safetensors", ".bin", ".pt", ".pth", ".onnx"}:
            raise AssertionError("Actual-SDK proof forbids model-weight files")
        if args[2] & write_flags and path != Path(os.devnull).resolve() and not path.is_relative_to(sandbox):
            raise AssertionError(f"Actual-SDK proof write outside owned sandbox: {path}")

    sys.addaudithook(audit)


def _installed_identity():
    from onprem.llm import LLM
    from onprem.llm import base
    from onprem.ingest.base import load_single_document
    from langchain_openai import ChatOpenAI
    from openai import OpenAI
    from openai.resources.chat.completions import Completions

    assert LLM is base.LLM and base.ChatOpenAI is ChatOpenAI
    targets = (
        ("onprem", "onprem", "onprem/llm/base.py", LLM.__init__),
        ("onprem-ingest", "onprem", "onprem/ingest/base.py", load_single_document),
        ("langchain-openai", "langchain-openai", "langchain_openai/chat_models/base.py", ChatOpenAI._generate),
        ("openai", "openai", "openai/_client.py", OpenAI.__init__),
        ("openai-completions", "openai", "openai/resources/chat/completions/completions.py", Completions.create),
    )
    identity = {}
    for name, distribution, relative, function in targets:
        installed = metadata.distribution(distribution)
        expected = Path(installed.locate_file(relative)).resolve()
        actual = Path(inspect.unwrap(function).__code__.co_filename).resolve()
        assert actual == expected, f"Wrong installed source for {name}: {actual} != {expected}"
        identity[name] = {"path": str(actual), "version": installed.version,
                          "sha256": hashlib.sha256(actual.read_bytes()).hexdigest()}
    return identity


def _child(artifacts):
    # Direct internal entry must also reject invalid configuration before imports.
    _timeout_seconds(os.environ.get(TIMEOUT_ENV))
    sandbox = artifacts / "sandbox"
    assert sys.flags.isolated and sys.dont_write_bytecode
    assert os.environ.get(PREREQUISITE) == "1"
    assert Path.cwd() == sandbox and Path.home() == sandbox / "home"
    (sandbox / "goals.example.yaml").write_bytes((ROOT / "goals.example.yaml").read_bytes())
    _guards(sandbox)
    # Public installed imports precede conftest; only this one file is selected.
    identity = _installed_identity()
    sys._zs_setup_sdk_identity = identity
    (sandbox / "source-identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    sys.path.insert(0, str(ROOT))
    import pytest

    return pytest.main([
        "-q", "-o", "addopts=", "-p", "no:cacheprovider",
        f"--rootdir={ROOT}", f"--confcutdir={ROOT / 'tests'}",
        f"--basetemp={sandbox / 'pytest'}", f"--junitxml={sandbox / 'junit.xml'}",
        str(TEST),
    ])


def _retain_cases(artifacts):
    junit = artifacts / "sandbox/junit.xml"
    if not junit.is_file():
        return False
    cases = ET.parse(junit).findall(".//testcase")
    rows = [{"classname": case.get("classname"), "name": case.get("name"),
             "failed": case.find("failure") is not None,
             "error": case.find("error") is not None,
             "skipped": case.find("skipped") is not None} for case in cases]
    (artifacts / "cases.json").write_text(json.dumps(rows, indent=2) + "\n")
    return (len(rows) == 17 and len({(r["classname"], r["name"]) for r in rows}) == 17
            and all(r["classname"] == "tests.test_setup_request_transport" for r in rows)
            and not any(r["failed"] or r["error"] or r["skipped"] for r in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    try:
        timeout = _timeout_seconds(os.environ.get(TIMEOUT_ENV))
    except ValueError as exc:
        parser.error(str(exc))
    allowed = (ROOT / "data/issue129-validation").resolve()
    artifacts = args.artifacts
    if (not artifacts.is_absolute() or artifacts != artifacts.resolve()
            or not artifacts.is_relative_to(allowed) or artifacts == allowed):
        parser.error("--artifacts must be a new absolute owned data/issue129-validation subdirectory")
    artifacts.mkdir()  # refuse overwriting any prior execution evidence
    sandbox = artifacts / "sandbox"
    sandbox.mkdir()
    env = _environment(sandbox)
    env[TIMEOUT_ENV] = str(timeout)  # only the admitted value enters the clean child
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
               "--isolated-child", str(artifacts)]
    (artifacts / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (artifacts / "pytest.log").open("w") as log:
        log.write(f"Actual-SDK safety-net: {timeout:g} seconds; maximum {MAX_TIMEOUT_SECONDS:g}.\n")
        log.flush()
        try:
            result = subprocess.run(command, cwd=sandbox, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            log.write(f"\nActual-SDK isolated child exceeded its {timeout:g}-second safety-net.\n")
            return 124
    complete = _retain_cases(artifacts)
    if result.returncode == 0 and not complete:
        print("Actual-SDK gate requires exactly 17 unique selected cases, zero failures/errors/skips", file=sys.stderr)
        return 1
    return result.returncode


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--isolated-child":
        raise SystemExit(_child(Path(sys.argv[2]).resolve()))
    raise SystemExit(main())
