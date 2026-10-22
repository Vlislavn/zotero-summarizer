"""One-shot dedicated-process dispatcher; no daemon, cursor or live mutations."""
from __future__ import annotations

import ctypes
import math
import os
import signal
import stat
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import psutil

from zotero_summarizer.services.run_log import make_run_id

from .analysis import _budgets as analysis_budgets
from .inputs import BudgetExceeded, _budgets as freeze_budgets, _no_symlinks
from .provenance import utc_iso
from .report import _private_directory, make_immutable, read_protocol, verify_report, write_protocol

DEFAULT_SEED = 42
SEED_ENV = "ZS_FEEDBACK_OPERATION_SEED"
DEFAULT_WALL_CAP_SECONDS = 120.0
MAX_WALL_CAP_SECONDS = 600.0
DEFAULT_RSS_BYTES = 512 * 1024**2
MAX_RSS_BYTES = 1024**3
DEFAULT_SWAP_GROWTH_BYTES = 2 * 1024**3
DEFAULT_MIN_FREE_RAM_PERCENT = 20.0
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_TERMINATE_GRACE_SECONDS = 1.0
MAX_IO_BYTES = 2 * 1024**3
MAX_SOURCE_FILES = 4096
MAX_CHILD_PROCESSES = 16
REAP_POLL_SECONDS = 0.05
BUDGET_ENV_PREFIX = "ZS_FEEDBACK_OPERATION_"
CHILD_KEYS = {"schema_version", "attempt_id", "run_id", "phase", "status", "error_class", "pid", "import_root",
              "output_manifest_sha256", "peak_rss_bytes"}
SETTINGS_KEYS = ("project_root", "data_dir", "config_path", "triage_db_path", "golden_csv_path", "interaction_log_path",
                 "classifier_runs_log_path", "feedback_analysis_dir")


@dataclass(frozen=True)
class OperationBudgets:
    wall_cap_seconds: float = DEFAULT_WALL_CAP_SECONDS
    max_rss_bytes: int = DEFAULT_RSS_BYTES
    max_swap_growth_bytes: int = DEFAULT_SWAP_GROWTH_BYTES
    min_free_ram_percent: float = DEFAULT_MIN_FREE_RAM_PERCENT
    poll_seconds: float = DEFAULT_POLL_SECONDS
    terminate_grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS
    max_io_bytes: int = MAX_IO_BYTES
    max_source_files: int = MAX_SOURCE_FILES

    def __post_init__(self):
        bounds = {"wall_cap_seconds": (0, MAX_WALL_CAP_SECONDS), "max_rss_bytes": (0, MAX_RSS_BYTES),
                  "max_swap_growth_bytes": (0, DEFAULT_SWAP_GROWTH_BYTES), "min_free_ram_percent": (20, 100),
                  "poll_seconds": (0, DEFAULT_POLL_SECONDS), "terminate_grace_seconds": (0, 5),
                  "max_io_bytes": (0, MAX_IO_BYTES), "max_source_files": (0, MAX_SOURCE_FILES)}
        for field in fields(self):
            value = getattr(self, field.name)
            minimum, maximum = bounds[field.name]
            numeric = type(value) in (int, float) and math.isfinite(value)
            if not numeric or value > maximum or value < minimum or (minimum == 0 and value == 0):
                raise ValueError(f"invalid operation budget: {field.name}")
            if isinstance(field.default, int) and type(value) is not int:
                raise ValueError(f"operation {field.name} must be an integer")


def resolve_budgets(value):
    if value is None:
        value = {}
    if type(value) is not dict or value.keys() - {"operation", "freeze", "analysis"}:
        raise ValueError("operation budgets require known operation/freeze/analysis dictionaries")
    environment = {}
    for field in fields(OperationBudgets):
        raw = os.environ.get(BUDGET_ENV_PREFIX + field.name.upper())
        if raw is not None:
            environment[field.name] = int(raw) if isinstance(field.default, int) else float(raw)
    OperationBudgets(**environment)  # malformed env is rejected even when overridden
    configured = value.get("operation", {})
    if isinstance(configured, OperationBudgets):
        configured = asdict(configured)
    if type(configured) is not dict or configured.keys() - {f.name for f in fields(OperationBudgets)}:
        raise ValueError("unknown operation budget")
    return {"operation": asdict(OperationBudgets(**(environment | configured))),
            "freeze": asdict(freeze_budgets(value.get("freeze"))),
            "analysis": asdict(analysis_budgets(value.get("analysis")))}


def resolve_seed(provenance):
    raw = os.environ.get(SEED_ENV)
    default = DEFAULT_SEED if raw is None else int(raw)
    if default < 0 or default >= 2**32:
        raise ValueError("operation seed must fit unsigned 32 bits")
    if type(provenance) is not dict or provenance.keys() - {"seed"}:
        raise ValueError("dispatcher provenance accepts only seed; system owns generated provenance")
    seed = provenance.get("seed", default)
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("operation seed must fit unsigned 32 bits")
    return seed


class OperationGuard:
    def __init__(self, budgets):
        self.budgets = budgets
        self.deadline = time.monotonic() + budgets["wall_cap_seconds"]
        self.swap_baseline = psutil.swap_memory().used
        self.io_bytes = 0
        self.peak_rss_bytes = 0

    def consume(self, size):
        self.io_bytes += size
        if self.io_bytes > self.budgets["max_io_bytes"]:
            raise BudgetExceeded("operation cumulative byte budget exhausted")
        self.check()

    def check(self, process=None):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("operation hard child/safety wall cap exhausted")
        memory = psutil.virtual_memory()
        if 100 * memory.available / memory.total < self.budgets["min_free_ram_percent"]:
            raise BudgetExceeded("operation available RAM floor breached")
        if psutil.swap_memory().used - self.swap_baseline > self.budgets["max_swap_growth_bytes"]:
            raise BudgetExceeded("operation swap growth cap breached")
        if process is not None and process.poll() is None:
            try:
                tree = psutil.Process(process.pid)
                owned = [tree, *tree.children(recursive=True)]
                if len(owned) > MAX_CHILD_PROCESSES:
                    raise BudgetExceeded("operation child process count cap breached")
                rss = sum(p.memory_info().rss for p in owned)
            except psutil.NoSuchProcess:
                rss = 0  # a process exiting during sampling is checked by poll/reap
            self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
            if rss > self.budgets["max_rss_bytes"]:
                raise BudgetExceeded("operation child tree RSS cap breached")


@contextmanager
def _signals():
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    def interrupt(signum, frame):
        interruption = KeyboardInterrupt() if signum == signal.SIGINT else InterruptedError("feedback operation interrupted")
        interruption.feedback_signal = signum
        raise interruption
    original = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    for signum in original:
        signal.signal(signum, interrupt)
    try:
        yield
    finally:
        foreign = False
        for signum, handler in original.items():
            if signal.getsignal(signum) is interrupt:
                signal.signal(signum, handler)
            else:
                foreign = True
        if foreign:
            raise RuntimeError("operation signal handler changed; restoration unavailable")




@contextmanager
def _single_run(root):
    try:
        import fcntl
    except ModuleNotFoundError as exc:
        raise NotImplementedError("feedback locking requires fcntl support") from exc
    lock = _no_symlinks(root / ".operation.lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_mode & 0o077:
            raise ValueError("operation lock must be private")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def _group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _reap(process, budgets):
    if _group_exists(process.pid):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=budgets["terminate_grace_seconds"])
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=budgets["terminate_grace_seconds"])
    if _group_exists(process.pid):
        os.killpg(process.pid, signal.SIGKILL)
        deadline = time.monotonic() + budgets["terminate_grace_seconds"]
        while _group_exists(process.pid) and time.monotonic() < deadline:
            time.sleep(min(REAP_POLL_SECONDS, budgets["poll_seconds"]))
    if _group_exists(process.pid):
        raise RuntimeError("operation child group cleanup incomplete")


def _child_command(request):
    root = Path(__file__).resolve().parents[4]
    bootstrap = ("import sys; sys.path.insert(0,sys.argv[1]); "
                 "from zotero_summarizer.services.golden.feedback_analysis.worker import main; "
                 "raise SystemExit(main(sys.argv[2]))")
    return [sys.executable, "-I", "-c", bootstrap, str(root), str(request)]


def _launch(attempt, request, guard, child):
    environment = {"PATH": os.defpath, "HOME": str(attempt), "TMPDIR": str(attempt), "LANG": "C.UTF-8",
                   "KMP_DUPLICATE_LIB_OK": "TRUE", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    environment["ZS_FEEDBACK_FREEZE_WALL_CAP_SECONDS"] = str(request["budgets"]["freeze"]["wall_cap_seconds"])
    environment["ZS_FEEDBACK_ANALYSIS_WALL_CAP_SECONDS"] = str(request["budgets"]["analysis"]["wall_cap_seconds"])
    process = subprocess.Popen(_child_command(attempt / "request.json"), cwd=Path(__file__).resolve().parents[4],
                               env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    child.update(pid=process.pid, reaped=False, group_absent=False)
    try:
        while process.poll() is None:
            guard.check(process)
            time.sleep(min(guard.budgets["poll_seconds"], max(0, guard.deadline - time.monotonic())))
    finally:
        _reap(process, guard.budgets)
        child.update(returncode=process.returncode, reaped=True, group_absent=True,
                     sampled_peak_rss_bytes=guard.peak_rss_bytes)
    guard.check()
    status = read_protocol(attempt / "child-status.json", guard)
    if type(status) is not dict or status.keys() != CHILD_KEYS or type(status["schema_version"]) is not int or status["schema_version"] != 1:
        raise ValueError("unsupported child protocol")
    if (status["attempt_id"], status["run_id"], status["pid"], status["import_root"]) != (
            request["attempt_id"], request["run_id"], process.pid, request["import_root"]):
        raise ValueError("child identity/import root mismatch")
    child.update(import_root=status["import_root"], phase=status["phase"], error_class=status["error_class"],
                 reported_peak_rss_bytes=status["peak_rss_bytes"],
                 output_manifest_sha256=status["output_manifest_sha256"])
    if process.returncode != 0 or status["status"] != "complete":
        errors = {c.__name__: c for c in (FileNotFoundError, PermissionError, ValueError, RuntimeError,
                                        TimeoutError, BudgetExceeded, sqlite3.DatabaseError, sqlite3.OperationalError)}
        raise errors.get(status["error_class"], ChildProcessError)("feedback child failed; see private attempt receipt")
    return status


def admit_sources(settings):
    root = _no_symlinks(settings.feedback_analysis_dir)
    for key in (*SETTINGS_KEYS[2:6], "classifier_runs_log_path"):
        source = _no_symlinks(getattr(settings, key))
        if source == root or source.is_relative_to(root):
            raise ValueError("operation output root cannot contain a live source/config")
    return root


def _attempt(settings, cutoff, seed, budgets):
    root = _private_directory(admit_sources(settings))
    attempts = _private_directory(root / "attempts")
    runs = _private_directory(root / "runs")
    attempt_id, run_id = make_run_id("feedback_attempt"), make_run_id("feedback_run")
    if any("/" in value or ".." in value for value in (attempt_id, run_id)):
        raise ValueError("unsafe generated operation ID")
    attempt = attempts / attempt_id
    attempt.mkdir(mode=0o700)
    request = {"schema_version": 1, "attempt_id": attempt_id, "run_id": run_id, "cutoff": cutoff, "seed": seed,
               "settings": {key: str(getattr(settings, key)) for key in SETTINGS_KEYS}, "budgets": budgets,
               "import_root": str(Path(__file__).resolve().parents[4])}
    return root, attempt, runs / run_id, request


def _rename_exclusive(source, destination):
    if sys.platform != "darwin":
        raise NotImplementedError("feedback atomic no-replace publication requires source-proven Darwin support")
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renamex_np
    except AttributeError as exc:
        raise NotImplementedError("Darwin atomic no-replace rename unavailable") from exc
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    rename_excl = 0x00000004  # public SDK sys/stdio.h RENAME_EXCL
    if rename(os.fsencode(source), os.fsencode(destination), rename_excl) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "feedback atomic no-replace publication failed")


def _publish(staging, destination, guard):
    _no_symlinks(destination)
    if destination.exists():
        raise FileExistsError("operation destination already exists")
    make_immutable(staging, guard, include_root=False)
    # macOS needs the source directory writable to move it between parents.
    _rename_exclusive(staging, destination)
    os.chmod(destination, 0o500)


def _publication_observation(staging, destination, identity):
    matches = []
    for path in (staging, destination):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            matches.append(False)
        except OSError as exc:
            return None, type(exc).__name__
        else:
            matches.append((metadata.st_dev, metadata.st_ino) == identity)
    if matches == [False, True]:
        return True, None
    if matches == [True, False]:
        return False, None
    return None, None


def run_feedback_analysis(settings, *, cutoff, provenance, budgets=None) -> dict:
    """Run local capture/analysis only in a clean externally bounded child."""
    return _run_feedback_analysis(settings, cutoff=cutoff, provenance=provenance, budgets=budgets)


def run_scheduled_feedback_analysis(settings, *, cutoff, provenance, schedule_intent, budgets=None) -> dict:
    from . import schedule_state
    schedule_state.main_thread_only()
    schedule_state.validate_intent(schedule_intent, cutoff)
    admit_sources(settings)
    with _single_run(schedule_state.private_root(settings)):
        return _run_feedback_analysis(settings, cutoff=cutoff, provenance=provenance, budgets=budgets,
                                      schedule_intent=schedule_intent)


def _run_feedback_analysis(settings, *, cutoff, provenance, budgets=None, schedule_intent=None, previous_cutoff=None):
    from . import schedule_state
    cutoff_iso = utc_iso(cutoff)
    configured, seed = resolve_budgets(budgets), resolve_seed(provenance)
    root, attempt, destination, request = _attempt(settings, cutoff_iso, seed, configured)
    receipt = {"schema_version": 1, "attempt_id": request["attempt_id"], "run_id": request["run_id"],
               "status": "failure", "published": False, "phase": "admission", "error_class": None,
               "attempt_path": str(attempt), "run_path": str(destination), "cutoff": cutoff_iso, "child": {},
               "measurement": "psutil tree RSS; available/total RAM; swap used delta; monotonic wall"}
    publication_identity = None
    try:
        guard = OperationGuard(configured["operation"])
        guard.check()
        with _signals(), _single_run(root):
            write_protocol(attempt / "request.json", request)
            if schedule_intent is not None:
                schedule_state.begin_attempt(settings, schedule_intent, attempt, request)
            receipt["phase"] = "child"
            child = _launch(attempt, request, guard, receipt["child"])
            receipt["phase"] = "validation"
            staging = attempt / "run-staging"
            identity = {k: request[k] for k in ("attempt_id", "run_id")}
            payload = verify_report(staging, child, identity, guard)
            prior = None
            if schedule_intent is not None:
                candidate, prior = schedule_state.prepare_publication(
                    settings, schedule_intent, attempt, payload, child, guard)
            if prior is None:
                guard.check()
                staged = _no_symlinks(staging).lstat()
                if not stat.S_ISDIR(staged.st_mode):
                    raise ValueError("publication staging must be a directory")
                publication_identity = (staged.st_dev, staged.st_ino)
                receipt["phase"] = "publication"
                _publish(staging, destination, guard)
                receipt.update(payload, published=True, phase="published")
        if prior is None:
            receipt["receipt_path"] = str(attempt / "receipt.json")
            write_protocol(attempt / "receipt.json", receipt)
        if schedule_intent is not None:
            return schedule_state.scheduled_receipt(settings, schedule_intent, attempt, candidate, guard,
                committed=prior is None, previous_cutoff=previous_cutoff)
        return receipt
    except BaseException as exc:
        if receipt["phase"] == "publication" and receipt["published"] is not True:
            receipt["published"], observation_error = _publication_observation(staging, destination, publication_identity)
            if observation_error is not None:
                receipt["publication_observation_error"] = observation_error
        receipt.update(status="failure", error_class=receipt["child"].get("error_class") or type(exc).__name__)
        if receipt["phase"] == "child" and receipt["child"].get("phase"):
            receipt["phase"] = receipt["child"]["phase"]
        receipt["publication_state"] = ("unknown" if receipt["published"] is None else
                                        "published_but_not_confirmed" if receipt["published"] else "not_published")
        receipt["receipt_path"] = str(attempt / "failure.json")
        persistence = {"status": "not_written", "error_class": None}
        receipt["receipt_persistence"] = persistence
        exc.feedback_receipt = receipt
        try:
            write_protocol(attempt / "failure.json", {**receipt, "receipt_persistence": {"status": "written", "error_class": None}})
        except BaseException as persistence_error:
            persistence.update(status="failed", error_class=type(persistence_error).__name__)
            if not isinstance(persistence_error, OSError):
                persistence_error.feedback_receipt = receipt
                raise
        else:
            persistence["status"] = "written"
        raise


__all__ = ["OperationBudgets", "run_feedback_analysis", "run_scheduled_feedback_analysis"]
