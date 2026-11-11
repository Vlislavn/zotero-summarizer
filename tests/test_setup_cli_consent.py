from zotero_summarizer.cli import _setup
from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
from zotero_summarizer.settings import Settings


def test_cli_inference_confirmation_default_no_and_saved(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    answers = iter(["anthropic", "TEST_API_KEY", "synthetic"])
    monkeypatch.setattr(_setup, "_prompt", lambda *a, **k: next(answers))
    seen = []
    monkeypatch.setattr(_setup, "_confirm", lambda label, *, default: seen.append((label, default)) or False)
    _setup._step_provider(settings)
    assert seen == [("Run real inference now (may load the configured model)?", False)]
    assert "synthetic" in settings.config_path.read_text()
