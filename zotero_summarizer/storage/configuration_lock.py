"""Nonwaiting cross-process configuration admission; never flock-wait on an event loop."""
from contextlib import contextmanager
import hashlib
import fcntl
import os
import time
from pathlib import Path
from threading import local

_OWNED = local()


def file_revision(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


@contextmanager
def configuration_lock(data_dir: Path, *, shared: bool = False, wait: float = 0):
    """Exclusive for writers; ``shared`` for read-only snapshots (readers never block each other).

    Nonwaiting unless a sync (worker-thread) caller opts into a bounded ``wait`` in seconds.
    """
    key = str(data_dir.absolute())
    held = getattr(_OWNED, 'paths', set())
    if key in held:
        yield  # Same synchronous owner; never inherited by another worker/thread.
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(data_dir / 'configuration.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        _OWNED.paths = held | {key}
        try:
            yield
        finally:
            _OWNED.paths = held
    finally:
        os.close(fd)
