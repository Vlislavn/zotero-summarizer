"""Bounded read-only policy files below the selected Settings.data_dir."""
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import stat

from zotero_summarizer.models.setup import SetupLoadApprovalPolicy
from zotero_summarizer.settings import Settings

MAX_POLICY_BYTES = 65_536


def _file_revision(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _regular_policy_bytes(settings: Settings) -> bytes:
    configured = Path(settings.setup_load_approval_policy_path)
    path = configured if configured.is_absolute() else settings.data_dir / configured
    parts = path.relative_to(settings.data_dir).parts
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("Policy must be below data_dir")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        directory = os.open(settings.data_dir, directory_flags)
        stack.callback(os.close, directory)
        for part in parts[:-1]:
            directory = os.open(part, directory_flags, dir_fd=directory)
            stack.callback(os.close, directory)
        before = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_POLICY_BYTES:
            raise ValueError("Policy must be a bounded nonempty regular file")
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        stack.callback(os.close, descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _file_revision(opened) != _file_revision(before):
            raise ValueError("Policy changed while opening")
        content = os.read(descriptor, MAX_POLICY_BYTES + 1)
        if len(content) != opened.st_size or _file_revision(os.fstat(descriptor)) != _file_revision(opened):
            raise ValueError("Policy changed while reading")
        return content


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate policy field")
        result[key] = value
    return result


def _reject_nonfinite(value: str):
    raise ValueError("Nonfinite policy value")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite policy value")
    return number


def read_approval_policy(settings: Settings) -> tuple[SetupLoadApprovalPolicy, str]:
    content = _regular_policy_bytes(settings)
    raw = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object,
                     parse_constant=_reject_nonfinite, parse_float=_finite_json_float)
    return SetupLoadApprovalPolicy.model_validate(raw), hashlib.sha256(content).hexdigest()


def policy_file_signature(settings: Settings) -> str:
    digest = hashlib.sha256()
    digest.update(repr((settings.setup_load_approval_policy_path,
                        settings.setup_load_approved_policy_sha256)).encode())
    if settings.setup_load_approval_policy_path is not None:
        try:
            digest.update(_regular_policy_bytes(settings))
        except (OSError, ValueError, TypeError) as exc:
            digest.update(f"policy-read-error:{type(exc).__name__}".encode())
    return digest.hexdigest()
