"""One existing-group command; no startup, selected synthetic root only."""
import json
from pathlib import Path

import pytest

from test_feedback_analysis_history import event, log, project
from zotero_summarizer.cli import build_parser, main


def test_help_has_one_analysis_command(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["goldenset", "analyze-feedback", "--help"])
    assert caught.value.code == 0
    args = build_parser().parse_args(["goldenset", "analyze-feedback", "--project-root", "/synthetic"])
    assert args.project_root == "/synthetic" and args.cutoff is None
    assert "--cutoff" in capsys.readouterr().out


def test_selected_root_actual_command_without_startup(tmp_path, capsys, monkeypatch):
    settings = project(tmp_path / "selected")
    log(settings, [event("A")])
    import zotero_summarizer.services.lifecycle as lifecycle
    def forbidden(*args, **kwargs):
        raise AssertionError("one-shot must not start app")
    monkeypatch.setattr(lifecycle, "startup", forbidden)

    code = main(["goldenset", "analyze-feedback", "--project-root", str(settings.project_root),
                 "--cutoff", "2026-10-08T02:00:00+02:00"])

    receipt = json.loads(capsys.readouterr().out)
    assert code == 0 and receipt["published"] is True
    assert Path(receipt["run_path"]).is_relative_to(settings.feedback_analysis_dir)
    assert receipt["cutoff"] == "2026-10-08T00:00:00Z"


def test_missing_db_prints_failure_receipt_and_propagates(tmp_path, capsys):
    with pytest.raises(FileNotFoundError):
        main(["goldenset", "analyze-feedback", "--project-root", str(tmp_path),
              "--cutoff", "2026-10-08T00:00:00Z"])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "failure" and receipt["published"] is False
    assert receipt["phase"] == "freeze" and Path(receipt["receipt_path"]).is_file()
    assert not (tmp_path / "data/triage_history.db").exists()


@pytest.mark.parametrize("cutoff", ["2026-10-08", "2026-10-08T00:00:00", "broken"])
def test_unaware_cutoff_is_parser_error(cutoff, tmp_path):
    with pytest.raises(SystemExit) as caught:
        main(["goldenset", "analyze-feedback", "--project-root", str(tmp_path), "--cutoff", cutoff])
    assert caught.value.code == 2
    assert not (tmp_path / "data").exists()
