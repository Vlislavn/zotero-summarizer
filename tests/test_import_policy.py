"""Exercise the actual architecture gate, including Python syntax alternatives."""
from pathlib import Path
import subprocess
import sys

import pytest

from tools.precommit import check_import_policy as policy


@pytest.mark.parametrize("layer", ["storage", "integrations", "mcp"])
@pytest.mark.parametrize("source", [
    "from ..services import config",
    "from zotero_summarizer import services as svc",
    "import os, zotero_summarizer.services.config as cfg",
    "from zotero_summarizer import (\n    services,\n)",
    "if True: import zotero_summarizer.services.config",
    "def lazy():\n    from ..services.config import load",
    "from ..services import *",
])
def test_forbidden_import_syntax_is_rejected(tmp_path, monkeypatch, capsys, layer, source):
    monkeypatch.chdir(tmp_path)
    path = Path(f"zotero_summarizer/{layer}/adapter.py")
    path.parent.mkdir(parents=True)
    path.write_text(source)

    assert policy.main([str(path)]) == 1
    assert "breaks layering" in capsys.readouterr().err


@pytest.mark.parametrize("source,expected", [
    ("from ...api import errors", 0),
    ("from ...api.errors import APIError", 0),
    ("import zotero_summarizer.api.errors as errors", 0),
    ("from ...api import app", 1),
    ("from ...api import other", 1),
    ("from zotero_summarizer import api", 1),
    ("from ...api import *", 1),
    ("from ... import api", 1),
    ("from ...api.errors_extra import Error", 1),
])
def test_services_can_import_only_api_errors(tmp_path, monkeypatch, source, expected):
    monkeypatch.chdir(tmp_path)
    path = Path("zotero_summarizer/services/library/__init__.py")
    path.parent.mkdir(parents=True)
    path.write_text(source)

    assert policy.main([str(path)]) == expected


@pytest.mark.parametrize("source", [
    "# from zotero_summarizer.services import config\npass",
    '"""Example:\nfrom zotero_summarizer.services import config\n"""',
    "from ..models import config",
    "from zotero_summarizer.services_extra import config",
])
def test_allowed_imports_and_non_code_are_not_rejected(tmp_path, monkeypatch, source):
    monkeypatch.chdir(tmp_path)
    path = Path("zotero_summarizer/storage/read.py")
    path.parent.mkdir(parents=True)
    path.write_text(source)

    assert policy.main([str(path)]) == 0


@pytest.mark.parametrize("absolute", [False, True])
def test_cli_rejects_nested_relative_import(tmp_path, absolute):
    script = Path(policy.__file__).resolve()
    path = tmp_path / "zotero_summarizer/mcp/nested/__init__.py"
    path.parent.mkdir(parents=True)
    path.write_text("from ...storage import repositories")

    result = subprocess.run(
        [sys.executable, str(script), str(path if absolute else path.relative_to(tmp_path))],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1
    assert "zotero_summarizer.storage.repositories" in result.stderr


def test_invalid_python_cannot_pass_the_gate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path("zotero_summarizer/storage/broken.py")
    path.parent.mkdir(parents=True)
    path.write_text("from ..services import (")

    assert policy.main([str(path)]) == 1


def test_top_level_service_structure_still_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path("zotero_summarizer/services/new_feature.py")
    path.parent.mkdir(parents=True)
    path.write_text("pass")

    assert policy.main([str(path)]) == 1
