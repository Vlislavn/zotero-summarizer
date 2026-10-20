"""Fresh-interpreter CLI config admission; synthetic roots, no providers or models."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARKER = "ZS_TEST_DEFAULT_ROOT_ONLY"


def invoke(tmp_path, body, *, shell=None, selected_env="ZS_OFFLINE=0\nAPP_LOG_LEVEL=WARNING\n", embedding_stub=False):
    default, selected = tmp_path / "default", tmp_path / "selected"
    default.mkdir()
    selected.mkdir()
    (default / ".env").write_text(f"{MARKER}=from_wrong_root\nZS_OFFLINE=1\nSUMMARY_TIMEOUT_SECONDS=123\n")
    (selected / ".env").write_text(selected_env)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    seconds = float(os.getenv("ZS_CLI_TEST_WALL_SECONDS", "10"))
    assert 0 < seconds <= 60
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ}
    environment.update(HOME=str(tmp_path / "home"), ZOTERO_SUMMARIZER_HOME=str(default),
                       PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
    environment.update(shell or {})
    code = "\n".join([
        "import sys,os,json,importlib.abc,socket",
        "from pathlib import Path",
        "sys.path[:0]=" + repr([str(ROOT), sysconfig.get_path("purelib")]),
        "class NoModels(importlib.abc.MetaPathFinder):",
        "    def find_spec(self,fullname,path=None,target=None):",
        "        if fullname.split('.')[0] in {'torch','transformers','sentence_transformers','huggingface_hub','onprem','telethon'}:",
        "            raise AssertionError('Forbidden model/transport import: '+fullname)",
        "sys.meta_path.insert(0,NoModels())",
        "import types; embedding=types.ModuleType('sentence_transformers')",
        "def no_weights(*args,**kwargs): raise AssertionError('Embedding model constructed by data-only migration')",
        "embedding.SentenceTransformer=no_weights",
        "sys.modules.update({'sentence_transformers':embedding} if " + repr(embedding_stub) + " else {})",
        "def no_network(*args,**kwargs): raise AssertionError('network attempted')",
        "socket.socket.connect=no_network",
        "selected=Path(sys.argv[1])",
        body,
    ])
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(selected)], env=environment,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=seconds)
    assert result.returncode == 0, result.stderr
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert all(after[name] == raw for name, raw in before.items())
    assert not (default / "data").exists()
    return json.loads(result.stdout.splitlines()[-1]), selected


def test_fresh_cli_import_does_not_load_unselected_dotenv_or_model_code(tmp_path):
    row, _ = invoke(tmp_path, "\n".join([
        "import zotero_summarizer.cli",
        "print(json.dumps({'wrong':os.getenv('ZS_TEST_DEFAULT_ROOT_ONLY'),'hf':os.getenv('HF_HUB_OFFLINE'),'offline':os.getenv('ZS_OFFLINE')}))",
    ]))
    assert row == {"wrong": None, "hf": None, "offline": None}


def test_help_does_not_load_any_project_environment(tmp_path):
    row, _ = invoke(tmp_path, "\n".join([
        "import zotero_summarizer.cli as cli",
        "try: cli.main(['--help'])",
        "except SystemExit as error: code=error.code",
        "else: raise AssertionError('Help did not finish through argparse')",
        "print(json.dumps({'exit':code,'wrong':os.getenv('ZS_TEST_DEFAULT_ROOT_ONLY'),'hf':os.getenv('HF_HUB_OFFLINE')}))",
    ]))
    assert row == {"exit": 0, "wrong": None, "hf": None}


def test_default_project_still_loads_its_offline_environment_before_handler(tmp_path):
    row, _ = invoke(tmp_path, "\n".join([
        "import zotero_summarizer.cli as cli",
        "from zotero_summarizer.cli import _app",
        "def handler(args):",
        "    assert os.getenv('ZS_TEST_DEFAULT_ROOT_ONLY')=='from_wrong_root'",
        "    assert os.getenv('HF_HUB_OFFLINE')=='1' and os.getenv('TRANSFORMERS_OFFLINE')=='1'",
        "    print(json.dumps({'selected_default_offline':True})); return 0",
        "_app._migrate=handler",
        "assert cli.main(['migrate'])==0",
    ]))
    assert row == {"selected_default_offline": True}


def test_invalid_budget_refuses_before_any_dotenv_or_settings_loading(tmp_path):
    row, _ = invoke(tmp_path, "\n".join([
        "import zotero_summarizer.cli as cli",
        "from zotero_summarizer.settings import Settings",
        "def refuse_settings(cls,**kwargs): raise AssertionError('Settings loaded before admission')",
        "Settings.load=classmethod(refuse_settings)",
        "try: cli.main(['research-feed','run','--from','2026-01-01','--to','2026-01-02','--shortlist-budget','0','--project-root',str(selected)])",
        "except SystemExit as error: code=error.code",
        "else: raise AssertionError('Invalid input accepted')",
        "print(json.dumps({'exit':code,'wrong':os.getenv('ZS_TEST_DEFAULT_ROOT_ONLY'),'hf':os.getenv('HF_HUB_OFFLINE')}))",
    ]))
    assert row == {"exit": 2, "wrong": None, "hf": None}


@pytest.mark.parametrize(("shell", "expected_timeout", "expected_hf"), [
    ({}, 420, None),
    ({"SUMMARY_TIMEOUT_SECONDS": "317"}, 317, None),
    ({"HF_HUB_OFFLINE": "1"}, 420, "1"),
])
def test_actual_migration_cli_uses_only_selected_root_and_existing_shell(tmp_path, shell, expected_timeout, expected_hf):
    row, selected = invoke(tmp_path, "\n".join([
        "import zotero_summarizer.cli as cli",
        "from zotero_summarizer.runtime import get_context",
        "code=cli.main(['migrate','--project-root',str(selected)])",
        "settings=get_context().settings",
        "print(json.dumps({'exit':code,'root':str(settings.project_root),'timeout':settings.summary_timeout_seconds,'hf':os.getenv('HF_HUB_OFFLINE'),'wrong':os.getenv('ZS_TEST_DEFAULT_ROOT_ONLY'),'triage':settings.triage_db_path.is_file(),'corpus':settings.corpus_db_path.is_file(),'data':str(settings.data_dir)}))",
    ]), shell=shell, embedding_stub=True)
    assert row == {"exit": 0, "root": str(selected), "timeout": expected_timeout,
                   "hf": expected_hf, "wrong": None, "triage": True, "corpus": True, "data": str(selected / "data")}


def test_moved_options_source_remains_bound_to_generation_identity(monkeypatch):
    from zotero_summarizer.services.faithbench import _options, _runner
    from tests.test_faithbench_runner import _fake_config, _generation_models

    config, models = _fake_config(), _generation_models(True)
    before = _runner.generation_identity(config, models)
    source = Path(_options.__file__).resolve()
    read_bytes = Path.read_bytes

    def changed(path):
        raw = read_bytes(path)
        return raw + b"\n" if path.resolve() == source else raw

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert _runner.generation_identity(config, models) != before


def test_existing_faithbench_exports_keep_exact_defining_objects():
    from importlib import import_module
    from zotero_summarizer.services import faithbench

    owners = {"ANSWER_PROMPT": "_runner", "PaperChunkIndex": "_corpus", "RunInputs": "_runner",
              "RunOptions": "_runner", "RunPaths": "_runner", "answer_with_retry": "_runner",
              "build_items": "_build_qa", "build_report": "_report", "judge_run": "_judge",
              "run_benchmark": "_runner", "select_papers": "_corpus"}
    assert faithbench.__all__ == list(owners)
    for name, module in owners.items():
        assert getattr(faithbench, name) is getattr(import_module(faithbench.__name__ + "." + module), name)
    with pytest.raises(AttributeError):
        getattr(faithbench, "no_such_public_export")


def test_existing_setup_exports_keep_exact_defining_objects():
    from importlib import import_module
    from zotero_summarizer.services import setup

    owners = {"bootstrap_phase0": "bootstrap", "detect_zotero_data_dirs": "detect",
              "get_setup_status": "status", "validate_config_draft": "validate", "write_env_paths": "env_writer"}
    assert setup.__all__ == list(owners)
    for name, module in owners.items():
        assert getattr(setup, name) is getattr(import_module(setup.__name__ + "." + module), name)
    with pytest.raises(AttributeError):
        getattr(setup, "no_such_public_export")


def test_selected_dotenv_offline_is_applied_before_handler_model_imports(tmp_path):
    row, _ = invoke(tmp_path, "\n".join([
        "import zotero_summarizer.cli as cli",
        "from zotero_summarizer.cli import _app",
        "def handler(args):",
        "    assert os.getenv('HF_HUB_OFFLINE')=='1' and os.getenv('TRANSFORMERS_OFFLINE')=='1'",
        "    assert os.getenv('ZS_TEST_DEFAULT_ROOT_ONLY') is None",
        "    print(json.dumps({'offline_at_handler':True})); return 0",
        "_app._migrate=handler",
        "assert cli.main(['migrate','--project-root',str(selected)])==0",
    ]), selected_env="ZS_OFFLINE=1\n")
    assert row == {"offline_at_handler": True}
