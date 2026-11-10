"""Salted credential fingerprint: lets an approval track one credential, never reveal it."""
import hashlib
import hmac
import os

from zotero_summarizer.services._common import settings
from zotero_summarizer.services.llm.credentials import get_api_key


def _salt() -> bytes:
    path = settings().data_dir / 'credential_fingerprint.salt'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # A concurrent writer created it.
        else:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(os.urandom(32).hex().encode())
    return path.read_bytes().strip()


def fingerprint(name: str) -> str | None:
    """HMAC-SHA256(local salt, current key value) for the slot; None when no key resolves."""
    resolved = get_api_key(name)
    return hmac.new(_salt(), resolved[0].encode(), hashlib.sha256).hexdigest() if resolved else None


def matches(name: str, expected: str) -> bool:
    current = fingerprint(name)
    return current is not None and hmac.compare_digest(current, expected)
