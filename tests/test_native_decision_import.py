"""The standalone comparator must import against the project's installed Pydantic."""
import subprocess
import sys
from pathlib import Path


def test_support_contract_import_and_absolute_file_admission():
    root = Path(__file__).resolve().parents[1]
    code = f"""import sys
sys.path.insert(0, {str(root)!r})
from pydantic import ValidationError
from tools.method_validation.support import FileRef
from pathlib import Path
record = {{'path': '/public/input.json', 'sha256': 'a' * 64}}
assert FileRef.model_validate(record).path == Path(record['path'])
try:
    FileRef.model_validate({{**record, 'path': 'relative/input.json'}})
except ValidationError:
    sys.exit(0)
raise AssertionError('a frozen input must not resolve relative to ambient cwd')
"""
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_module_cli_recorded_run_inspect_and_exclusive_owner(tmp_path):
    from test_support_fixtures import fixture

    _, _, _, config = fixture(tmp_path)
    root = Path(__file__).resolve().parents[1]

    def invoke(arguments):
        code = (f'import runpy,sys;sys.path.insert(0,{str(root)!r});'
                f'sys.argv=["tools.method_validation",*{arguments!r}];'
                'runpy.run_module("tools.method_validation",run_name="__main__")')
        return subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=root,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True)

    admitted = invoke(['support', '--config', str(config)])
    assert admitted.returncode == 0, admitted.stdout + admitted.stderr
    assert not (tmp_path / 'selected').exists()
    completed = invoke(['support', '--config', str(config), '--run'])
    assert completed.returncode == 0, completed.stdout + completed.stderr
    owner = tmp_path / 'selected/data/support_comparison/public-attempt'
    assert (owner / 'ACK').is_file() and (owner / 'report.json').is_file()
    before = {p.name: p.read_bytes() for p in owner.iterdir()}
    inspected = invoke(['support', '--inspect', str(owner)])
    assert inspected.returncode == 0, inspected.stdout + inspected.stderr
    assert {p.name: p.read_bytes() for p in owner.iterdir()} == before
    repeated = invoke(['support', '--config', str(config), '--run'])
    assert repeated.returncode != 0
    assert {p.name: p.read_bytes() for p in owner.iterdir()} == before
