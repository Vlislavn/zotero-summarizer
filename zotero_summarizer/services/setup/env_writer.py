"""Write the path-only keys (``PDF_ROOT`` / ``ZOTERO_DATA_DIR``) into ``.env``.

SANCTIONED EXCEPTION to the "all state under data/" rule: these two keys are
filesystem locations the app needs *before* Settings loads, so they live in
``.env`` (read by ``Settings.load`` at startup) — see ``services/setup/README.md``.

Two load-bearing properties:

1. **Allowlist.** Only ``_ALLOWED_ENV_KEYS`` may be written. Any other key raises
   ``APIError`` (422) — this writer must never touch secret keys (``OPENAI_API_KEY``
   etc.) or arbitrary config.
2. **Byte-for-byte preservation.** A read-modify-write that replaces ONLY the
   allowlisted lines and re-emits every other line (secrets, comments, blanks)
   exactly as found. We do NOT round-trip through ``dotenv`` dump, which would
   reorder/normalize/quote and could mangle a secret line.

Paths are validated to exist on disk before any write (422 otherwise) — a setup
flow that points the app at a non-existent dir is a user error, not a silent
"create it for them" action.
"""
from __future__ import annotations

from pathlib import Path

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.setup import UpdatePathsResponse, UpdatePathsValidation
from zotero_summarizer.services._common import atomic_write


# The ONLY keys this writer may set. Frozen — never widen to cover secrets.
_ALLOWED_ENV_KEYS: tuple[str, ...] = ("PDF_ROOT", "ZOTERO_DATA_DIR")


def _reject_unknown_keys(updates: dict[str, str]) -> None:
    bad = [key for key in updates if key not in _ALLOWED_ENV_KEYS]
    if bad:
        raise APIError(
            error="validation_error",
            message=f"keys not allowed for .env writes: {sorted(bad)}; allowed: {list(_ALLOWED_ENV_KEYS)}",
            status_code=422,
            details={"rejected_keys": sorted(bad), "allowed_keys": list(_ALLOWED_ENV_KEYS)},
        )


def _validate_paths_exist(updates: dict[str, str]) -> None:
    """Each supplied path must exist on disk before we persist it."""
    for key, raw in updates.items():
        path = Path(raw).expanduser()
        if not path.exists():
            raise APIError(
                error="validation_error",
                message=f"{key} path does not exist on disk: {raw}",
                status_code=422,
                details={"key": key, "path": raw},
            )


def _rewrite_env_lines(existing: str, updates: dict[str, str]) -> str:
    """Return the new ``.env`` body: replace the allowlisted keys' lines in place,
    append any not already present, and re-emit ALL other lines byte-for-byte.

    Matching is on a leading ``KEY=`` (after stripping leading whitespace). A
    commented ``# KEY=`` line is NOT a match — it's preserved as-is and the real
    assignment is appended, so we never uncomment a user's deliberately-disabled
    line.
    """
    remaining = dict(updates)
    out_lines: list[str] = []

    # ``splitlines`` preserves every interior line verbatim (it only drops line
    # terminators). We replace matched keys in place and re-emit all other lines
    # byte-for-byte, so secrets/comments/blanks are untouched.
    for line in existing.splitlines():
        stripped = line.lstrip()
        matched_key = next((key for key in remaining if stripped.startswith(f"{key}=")), None)
        if matched_key is not None:
            out_lines.append(f"{matched_key}={remaining.pop(matched_key)}")
        else:
            out_lines.append(line)

    # Keys not already present in the file are appended.
    for key, value in remaining.items():
        out_lines.append(f"{key}={value}")

    if not out_lines:
        return ""
    # A well-formed .env ends with exactly one trailing newline. The interior is
    # byte-identical to the input (modulo the replaced/appended key lines).
    return "\n".join(out_lines) + "\n"


def write_env_paths(env_path: Path, updates: dict[str, str]) -> UpdatePathsResponse:
    """Persist the allowlisted path keys into ``env_path`` atomically.

    Validates the allowlist + path existence FIRST (raising 422 before any write),
    then does a byte-preserving read-modify-write via ``atomic_write``. Returns the
    keys written + a re-derived validation snapshot of the new values.
    """
    _reject_unknown_keys(updates)
    if not updates:
        # Nothing to write — but still report a validation snapshot of the
        # currently-configured values so the caller has a consistent shape.
        return UpdatePathsResponse(
            written=[],
            validated=_validation_snapshot(updates),
        )
    _validate_paths_exist(updates)

    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    new_body = _rewrite_env_lines(existing, updates)

    def _write(tmp: Path) -> None:
        tmp.write_text(new_body, encoding="utf-8")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(env_path, _write)

    return UpdatePathsResponse(
        written=list(updates.keys()),
        validated=_validation_snapshot(updates),
    )


def _validation_snapshot(updates: dict[str, str]) -> UpdatePathsValidation:
    """Re-derive the path-validity flags from the values just written.

    ``pdf_root_exists`` and ``zotero_db_found`` reflect the SUBMITTED values (the
    new ones), not the still-old live ``Settings`` — the change needs a restart to
    take effect, so the UI should preview the new state, not the stale one.
    """
    pdf_raw = updates.get("PDF_ROOT")
    zotero_raw = updates.get("ZOTERO_DATA_DIR")
    pdf_exists = bool(pdf_raw) and Path(pdf_raw).expanduser().exists()
    zotero_db_found = bool(zotero_raw) and (Path(zotero_raw).expanduser() / "zotero.sqlite").exists()
    return UpdatePathsValidation(
        pdf_root_exists=bool(pdf_exists),
        zotero_db_found=bool(zotero_db_found),
    )
