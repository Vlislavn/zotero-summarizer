"""Finite owned-process guard; the pipe sentinel survives abrupt monitor death.

Reuses the application's qualified operational guard/reaper mechanics. Resource
RSS defaults to 4GiB and may be explicitly scoped by the operator. Available-RAM,
swap-growth and cleanup limits remain fixed; every run has a finite deadline.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import psutil

_LIMITS = {"rss_mib": 4096, "swap_growth_mib": 2048, "min_free_percent": 20,
           "poll_seconds": 1, "terminate_grace_seconds": 2}
_OWNER = "local-evaluation-v1"


def _limits(rss_limit_mib):
    if type(rss_limit_mib) is not int or rss_limit_mib <= 0:
        raise ValueError('A finite positive integer RSS limit is required')
    return {**_LIMITS, 'rss_mib': rss_limit_mib}


def _identity(pid):
    return {"pid": pid, "created": psutil.Process(pid).create_time()}


def _same_process(record):
    try:
        process = psutil.Process(record["pid"])
        return process if process.create_time() == record["created"] else None
    except psutil.NoSuchProcess:
        return None


def _members(root):
    current = _same_process(root)
    if psutil.pid_exists(root["pid"]) and current is None:
        return []
    found = {p.pid: p for p in current.children(recursive=True)} if current else {}
    for process in psutil.process_iter(["pid", "create_time"]):
        try:
            if os.getpgid(process.pid) == root["pid"] and process.create_time() >= root["created"]:
                found[process.pid] = process
        except (ProcessLookupError, psutil.NoSuchProcess):
            continue
    return list(found.values())


def _reap(root, *, command=None):
    members = _members(root)
    for process in members:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue
    if command is not None:
        # Preserve the direct child's real wait status before psutil can reap it.
        try:
            command.wait(timeout=_LIMITS["terminate_grace_seconds"])
        except subprocess.TimeoutExpired:
            command.kill()
            command.wait()
    _, alive = psutil.wait_procs(members, timeout=_LIMITS["terminate_grace_seconds"])
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
    psutil.wait_procs(alive, timeout=_LIMITS["terminate_grace_seconds"])


def _sentinel(read_fd, root):
    try:
        with os.fdopen(read_fd, "rb") as stream:
            stream.read()
    finally:
        _reap(root)


def _sample(root, initial_swap):
    memory = psutil.virtual_memory()
    monitor = psutil.Process()
    members = {p.pid: p for p in [monitor, *monitor.children(recursive=True), *_members(root)]}
    processes = []
    for process in members.values():
        try:
            processes.append({'pid': process.pid, 'created': process.create_time(), 'name': process.name(),
                              'rss_mib': process.memory_info().rss / 1024**2})
        except psutil.NoSuchProcess:
            continue
    return {"rss_mib": sum(row['rss_mib'] for row in processes),
            "processes": processes,
            "swap_growth_mib": max(0, psutil.swap_memory().used - initial_swap) / 1024**2,
            "free_percent": 100 * memory.available / memory.total}


def _tripwire(observed, limits):
    for key in ("rss_mib", "swap_growth_mib"):
        if observed[key] > limits[key]:
            return key
    return "free_percent" if observed["free_percent"] < limits["min_free_percent"] else None


def _persist(path, value):
    temporary = path.with_suffix(".writing")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _monitor(child, watchdog, record, receipt, stopped, baseline):
    started, initial_swap = baseline
    while True:
        observed = _sample(record["child"], initial_swap)
        elapsed = time.monotonic() - started
        if observed['rss_mib'] >= record.get('peak_rss_mib', 0):
            record['peak_rss_processes'] = observed['processes']
        record.update(observation=observed, elapsed_seconds=round(elapsed, 2),
                      peak_rss_mib=max(record.get("peak_rss_mib", 0), observed["rss_mib"]),
                      min_free_percent=min(record.get("min_free_percent", 100), observed["free_percent"]),
                      max_swap_growth_mib=max(record.get("max_swap_growth_mib", 0), observed["swap_growth_mib"]))
        reason = _tripwire(observed, record['limits'])
        if watchdog.poll() is not None:
            raise RuntimeError("Sentinel exited before owned work was reaped")
        if elapsed >= record["limits"]["wall_seconds"]:
            reason = "wall_seconds"
        if stopped:
            reason = "explicit_stop"
        if reason or child.poll() is not None:
            record["guard_stop"] = reason
            return
        _persist(receipt, record)
        time.sleep(_LIMITS["poll_seconds"])


def _run(args):
    limits = _limits(args.rss_mib)
    if not 1 <= args.wall_seconds <= 3600:
        raise ValueError("Finite wall deadline must be in [1, 3600] seconds")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ValueError("An explicit finite command is required")
    log, receipt = Path(args.log), Path(args.receipt)
    if receipt.exists():
        raise ValueError("Use a fresh receipt; never overwrite a previous run identity")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    memory = psutil.virtual_memory()
    if 100 * memory.available / memory.total < _LIMITS["min_free_percent"]:
        _persist(receipt, {"owner": _OWNER, "state": "refused", "reason": "free_percent"})
        return 125
    stopped, previous = [], {}
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[number] = signal.signal(number, lambda signum, _frame: stopped.append(signum))
    child = watchdog = None
    write_fd = None
    record = {"owner": _OWNER, "mode": "finite", "state": "starting", "guard_stop": None,
              "limits": {**limits, "wall_seconds": args.wall_seconds},
              "guardian": _identity(os.getpid()), "command": command, "cwd": str(Path(args.cwd).resolve())}
    started, initial_swap = time.monotonic(), psutil.swap_memory().used
    try:
        _persist(receipt, record)
        with log.open("xb") as output:
            child = subprocess.Popen(command, cwd=args.cwd, stdout=output, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            record["child"] = _identity(child.pid)
            read_fd, write_fd = os.pipe()
            try:
                watchdog = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--sentinel",
                    str(read_fd), str(child.pid), str(record["child"]["created"])], pass_fds=(read_fd,),
                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            finally:
                os.close(read_fd)
            record.update(state="running", sentinel=_identity(watchdog.pid))
            _persist(receipt, record)
            _monitor(child, watchdog, record, receipt, stopped, (started, initial_swap))
    except BaseException as error:
        record.update(guard_stop="monitor_failure", monitor_error=type(error).__name__)
    finally:
        if child is not None:
            _reap(record["child"], command=child)
            record["returncode"] = child.wait(timeout=5)
        if write_fd is not None:
            os.close(write_fd)
        if watchdog is not None:
            watchdog.wait(timeout=6)
        record.update(state="stopped", elapsed_seconds=round(time.monotonic() - started, 2))
        _persist(receipt, record)
        for number, handler in previous.items():
            signal.signal(number, handler)
    return 125 if record["guard_stop"] else record.get("returncode", 0)


def _main():
    if sys.argv[1:2] == ["--sentinel"]:
        _sentinel(int(sys.argv[2]), {"pid": int(sys.argv[3]), "created": float(sys.argv[4])})
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--wall-seconds", type=int, default=900)
    parser.add_argument("--rss-mib", type=int, default=4096,
                        help="Explicit operator-selected owned RSS cap; other safeguards remain fixed")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return _run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(_main())
