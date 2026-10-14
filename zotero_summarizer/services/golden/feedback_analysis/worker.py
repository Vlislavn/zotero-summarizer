"""Strict, offline, clean-process capture/analysis entry; never app startup."""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import resource
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from .analysis import RULES, analyze_inputs
from .inputs import _no_symlinks, freeze_inputs
from .operation import OperationGuard, SETTINGS_KEYS, resolve_budgets
from .provenance import canonical_bytes
from .report import hash_file, read_protocol, stage_report, write_protocol

REQUEST_KEYS = {"schema_version", "attempt_id", "run_id", "cutoff", "seed", "settings", "budgets", "import_root"}
DEPENDENCIES = ("psutil", "python-dotenv", "pydantic", "pydantic-core", "PyYAML", "scipy", "numpy")
SOURCE_MAX_BYTES = 64 * 1024**2
CONFIG_MAX_BYTES = 1024**2
CODE_REVISION_TIMEOUT_SECONDS = 5.0


def _offline():
    original, original_ex = socket.socket.connect, socket.socket.connect_ex
    def blocked(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise PermissionError("feedback analysis has no TCP/network capability")
        return original(self, address)
    def blocked_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise PermissionError("feedback analysis has no TCP/network capability")
        return original_ex(self, address)
    socket.socket.connect, socket.socket.connect_ex = blocked, blocked_ex


def _request(path):
    # Admission protocol has its own fixed small bound before caller budgets can be trusted.
    guard = OperationGuard(resolve_budgets(None)["operation"])
    request = read_protocol(path, guard)
    if type(request) is not dict or request.keys() != REQUEST_KEYS or type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise ValueError("unsupported child request version")
    root = Path(__file__).resolve().parents[4]
    if request["import_root"] != str(root):
        raise ValueError("actual child import root differs from request")
    if any(type(request[k]) is not str or not request[k] or "/" in request[k] or ".." in request[k]
           for k in ("attempt_id", "run_id")):
        raise ValueError("unsafe child identity")
    if type(request["seed"]) is not int or not 0 <= request["seed"] < 2**32:
        raise ValueError("invalid child seed")
    if type(request["settings"]) is not dict or request["settings"].keys() != set(SETTINGS_KEYS):
        raise ValueError("invalid child Settings paths")
    if any(type(value) is not str or not Path(value).is_absolute() for value in request["settings"].values()):
        raise ValueError("child Settings paths must be absolute")
    settings = SimpleNamespace(**{key: _no_symlinks(value) for key, value in request["settings"].items()})
    if settings.feedback_analysis_dir != settings.data_dir / "feedback-analysis":
        raise ValueError("child output root differs from Settings")
    attempt = settings.feedback_analysis_dir / "attempts" / request["attempt_id"]
    if path != attempt / "request.json":
        raise ValueError("child request path differs from Settings attempt")
    if request["budgets"] != resolve_budgets(request["budgets"]):
        raise ValueError("child budgets must be fully resolved")
    return request, settings, attempt, root


def _source_paths(root, guard):
    pending, count = [root / "zotero_summarizer"], 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                count += 1
                guard.consume(0)
                if count > guard.budgets["max_source_files"]:
                    raise ValueError("source inventory entry count budget exhausted")
                if entry.is_symlink():
                    raise ValueError("symlink in application source inventory")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.name.endswith(".py"):
                    yield Path(entry.path)


def _code_identity(root, guard):
    files, total = {}, 0
    for path in _source_paths(root, guard):
        record = hash_file(path, guard, max_bytes=SOURCE_MAX_BYTES - total)
        total += record["byte_size"]
        if total > SOURCE_MAX_BYTES:
            raise ValueError("source inventory byte budget exhausted")
        files[path.relative_to(root).as_posix()] = record
    if not files:
        raise ValueError("empty code inventory")
    for name in ("pyproject.toml", "uv.lock"):
        files[name] = hash_file(root / name, guard, max_bytes=SOURCE_MAX_BYTES - total)
        total += files[name]["byte_size"]
        if total > SOURCE_MAX_BYTES:
            raise ValueError("source inventory byte budget exhausted")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True,
                            text=True, timeout=min(CODE_REVISION_TIMEOUT_SECONDS, guard.budgets["wall_cap_seconds"])).stdout.strip()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("full code commit identity unavailable")
    return {"commit": commit, "sha256": hashlib.sha256(canonical_bytes(files)).hexdigest(),
            "inventory": files, "import_root": str(root), "scope": "actual_application_python_sources_and_dependency_files"}


def _provenance(request, settings, root, guard):
    code = _code_identity(root, guard)
    goals = {"status": "missing", "sha256": None, "byte_size": 0}
    try:
        record = hash_file(settings.config_path, guard, max_bytes=CONFIG_MAX_BYTES)
    except FileNotFoundError:
        if settings.config_path.exists():
            raise
    else:
        goals = {"status": "present", **record}
    known = {"budgets": request["budgets"], "rules": RULES, "goals_file": goals, "seed": request["seed"]}
    return {"code": code, "config": {"sha256": hashlib.sha256(canonical_bytes(known)).hexdigest(),
                "goals_file": goals, "resolved": request["budgets"],
                "known_scope": "feedback_operation_freeze_analysis_bounds_and_rules_plus_selected_goals_bytes",
                "unknown_scope": ["resolved_live_model_config", "historical_inference_config", "historical_training_config"]},
            "environment": {"python": platform.python_version(), "executable": sys.executable,
                            "sqlite": sqlite3.sqlite_version,
                            "packages": {name: importlib.metadata.version(name) for name in DEPENDENCIES}},
            "seed": request["seed"], "inference_identity": {"status": "unknown", "scope": "historical_inference_not_current_gate"}}


def _import_proof(root):
    files = {}
    for name, module in tuple(sys.modules.items()):
        if name == "zotero_summarizer" or name.startswith("zotero_summarizer."):
            path = getattr(module, "__file__", None)
            if path is not None:
                actual = Path(path).resolve()
                if not actual.is_relative_to(root / "zotero_summarizer"):
                    raise ValueError("foreign application source imported in child")
                files[name] = str(actual)
    return files


def main(request_path):
    _offline()
    path = _no_symlinks(request_path)
    status = {"schema_version": 1, "attempt_id": path.parent.name, "run_id": None, "phase": "request",
              "status": "failure", "error_class": None, "pid": os.getpid(),
              "import_root": str(Path(__file__).resolve().parents[4]), "output_manifest_sha256": None, "peak_rss_bytes": None}
    try:
        request, settings, attempt, root = _request(path)
        status.update(attempt_id=request["attempt_id"], run_id=request["run_id"], phase="provenance")
        guard = OperationGuard(request["budgets"]["operation"])
        provenance = _provenance(request, settings, root, guard)
        staging = attempt / "run-staging"
        staging.mkdir(mode=0o700)
        status["phase"] = "freeze"
        from datetime import datetime
        freeze_inputs(settings, staging / "retained", cutoff=datetime.fromisoformat(request["cutoff"].replace("Z", "+00:00")),
                      provenance=provenance, budgets=request["budgets"]["freeze"])
        status["phase"] = "analysis"
        result = analyze_inputs(staging / "retained", budgets=request["budgets"]["analysis"])
        status["phase"] = "report"
        write_protocol(attempt / "imports.json", _import_proof(root))
        stage_report(staging, result, {k: request[k] for k in ("attempt_id", "run_id")}, guard)
        status.update(output_manifest_sha256=hash_file(staging / "output-manifest.json", guard)["sha256"],
                      status="complete", phase="complete")
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        status["peak_rss_bytes"] = int(peak if sys.platform == "darwin" else peak * 1024)
        write_protocol(attempt / "child-status.json", status)
        return 0
    except BaseException as exc:
        status["error_class"] = type(exc).__name__
        write_protocol(path.parent / "child-status.json", status)
        return 1
