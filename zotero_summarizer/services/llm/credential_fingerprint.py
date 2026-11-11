"""Salted credential fingerprint: lets an approval track one credential, never reveal it."""
import hashlib
import hmac
import os

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services._common import settings
from zotero_summarizer.services.llm.credentials import get_api_key


def _salt() -> bytes:
    path = settings().data_dir / 'credential_fingerprint.salt'
    if not path.exists() or not path.read_bytes().strip():  # missing, or left empty by an interrupted writer
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(os.urandom(32).hex().encode())
        try:
            os.link(temporary, path)  # first writer wins; a concurrent one keeps the existing salt
        except FileExistsError:
            if not path.read_bytes().strip():
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path.read_bytes().strip()


def fingerprint(name: str) -> str | None:
    """HMAC-SHA256(local salt, current key value) for the slot; None when no key resolves."""
    try:
        resolved = get_api_key(name)
    except APIError:  # a slot name the credential store rejects can never match an approval
        return None
    return hmac.new(_salt(), resolved[0].encode(), hashlib.sha256).hexdigest() if resolved else None


def matches(name: str, expected: str) -> bool:
    current = fingerprint(name)
    return current is not None and hmac.compare_digest(current, expected)
