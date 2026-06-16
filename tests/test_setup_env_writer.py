"""LOAD-BEARING: the .env path writer must preserve every other line byte-for-byte
(especially secret lines), reject non-allowlisted keys, and reject non-existent
paths. A regression here could clobber a user's secrets or silently widen the
write surface to a secret key — both unacceptable.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services.setup.env_writer import _ALLOWED_ENV_KEYS, write_env_paths


def test_writing_paths_preserves_all_other_lines_byte_for_byte(tmp_path: Path):
    env = tmp_path / ".env"
    # A secret line, a comment, a blank line, and a pre-existing PDF_ROOT to be
    # replaced in place. Every non-allowlisted line must survive verbatim.
    original = (
        "# my env\n"
        "OPENAI_API_KEY=sk-test-DO-NOT-TOUCH\n"
        "\n"
        "PDF_ROOT=/old/pdf/root\n"
        "CUSTOM_BASE_URL=https://x.example/v1\n"
    )
    env.write_text(original, encoding="utf-8")

    new_pdf = tmp_path / "pdfs"
    new_pdf.mkdir()
    new_zot = tmp_path / "zot"
    new_zot.mkdir()

    result = write_env_paths(env, {"PDF_ROOT": str(new_pdf), "ZOTERO_DATA_DIR": str(new_zot)})
    assert set(result.written) == {"PDF_ROOT", "ZOTERO_DATA_DIR"}

    text = env.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Secret line untouched, byte-for-byte.
    assert "OPENAI_API_KEY=sk-test-DO-NOT-TOUCH" in lines
    # Comment + blank + other key preserved verbatim.
    assert "# my env" in lines
    assert "" in lines
    assert "CUSTOM_BASE_URL=https://x.example/v1" in lines
    # PDF_ROOT replaced in place (not duplicated); ZOTERO_DATA_DIR appended.
    assert f"PDF_ROOT={new_pdf}" in lines
    assert sum(1 for ln in lines if ln.startswith("PDF_ROOT=")) == 1
    assert f"ZOTERO_DATA_DIR={new_zot}" in lines
    # The secret value never appears mangled or duplicated.
    assert text.count("sk-test-DO-NOT-TOUCH") == 1


def test_commented_path_key_is_not_uncommented(tmp_path: Path):
    """A commented ``# PDF_ROOT=`` line is NOT treated as the key — it's preserved
    and the real assignment is appended (we never resurrect a disabled line)."""
    env = tmp_path / ".env"
    env.write_text("# PDF_ROOT=/disabled\nOPENAI_API_KEY=sk-keep\n", encoding="utf-8")
    new_pdf = tmp_path / "pdfs"
    new_pdf.mkdir()

    write_env_paths(env, {"PDF_ROOT": str(new_pdf)})
    lines = env.read_text(encoding="utf-8").splitlines()
    assert "# PDF_ROOT=/disabled" in lines  # comment preserved
    assert f"PDF_ROOT={new_pdf}" in lines    # real assignment appended
    assert "OPENAI_API_KEY=sk-keep" in lines


def test_non_allowlisted_key_is_rejected_422(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    with pytest.raises(APIError) as excinfo:
        write_env_paths(env, {"OPENAI_API_KEY": "sk-evil"})
    assert excinfo.value.status_code == 422
    # The file is untouched (rejection happens before any write).
    assert env.read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-test\n"


def test_secret_key_can_never_be_in_the_allowlist():
    # Defensive: the allowlist is exactly the two path keys, no secret key.
    assert _ALLOWED_ENV_KEYS == ("PDF_ROOT", "ZOTERO_DATA_DIR")
    assert "OPENAI_API_KEY" not in _ALLOWED_ENV_KEYS


def test_nonexistent_path_is_rejected_422(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    missing = tmp_path / "does-not-exist"
    with pytest.raises(APIError) as excinfo:
        write_env_paths(env, {"PDF_ROOT": str(missing)})
    assert excinfo.value.status_code == 422
    # No partial write happened.
    assert env.read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-test\n"


def test_creates_env_when_absent(tmp_path: Path):
    env = tmp_path / ".env"  # does not exist yet
    new_zot = tmp_path / "zot"
    new_zot.mkdir()
    result = write_env_paths(env, {"ZOTERO_DATA_DIR": str(new_zot)})
    assert result.written == ["ZOTERO_DATA_DIR"]
    assert env.exists()
    assert env.read_text(encoding="utf-8") == f"ZOTERO_DATA_DIR={new_zot}\n"


def test_validation_snapshot_reflects_submitted_values(tmp_path: Path):
    env = tmp_path / ".env"
    pdf = tmp_path / "pdfs"
    pdf.mkdir()
    zot = tmp_path / "zot"
    zot.mkdir()
    (zot / "zotero.sqlite").write_bytes(b"")  # makes zotero_db_found True
    result = write_env_paths(env, {"PDF_ROOT": str(pdf), "ZOTERO_DATA_DIR": str(zot)})
    assert result.validated.pdf_root_exists is True
    assert result.validated.zotero_db_found is True
    assert result.restart_required is True
