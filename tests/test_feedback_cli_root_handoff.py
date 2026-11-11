"""Default-root handoff controls reused from the published CLI prerequisite."""
import pytest

from test_cli_selected_environment import invoke


@pytest.mark.parametrize("from_cwd", [False, True])
def test_default_root_cannot_be_redirected_by_its_dotenv(tmp_path, from_cwd):
    row, selected = invoke(tmp_path, "\n".join([
        "import zotero_summarizer.cli as cli",
        "from zotero_summarizer.runtime import get_context",
        *(["os.environ.pop('ZOTERO_SUMMARIZER_HOME')", "os.chdir(selected)",
           "(selected/'goals.yaml').write_text('')"] if from_cwd else []),
        "assert cli.main(['migrate'])==0",
        "settings=get_context().settings",
        "print(json.dumps({'root':str(settings.project_root),'triage':settings.triage_db_path.is_file(),'corpus':settings.corpus_db_path.is_file()}))",
    ]), shell={"ZOTERO_SUMMARIZER_HOME": str(tmp_path / "selected")},
        selected_env=f"ZOTERO_SUMMARIZER_HOME={tmp_path / 'default'}\nZS_OFFLINE=1\n", embedding_stub=True)
    assert row == {"root": str(selected), "triage": True, "corpus": True}
