"""Phase-0 bootstrap: absent goals.yaml/.env are created with SAFE defaults;
present files are left untouched; the DB migration runs when the triage DB is
absent. Idempotent + non-destructive."""
from __future__ import annotations

from pathlib import Path

from zotero_summarizer.services._common import read_config
from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
from zotero_summarizer.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings.load(project_root=tmp_path)


def test_creates_goals_and_env_when_absent(tmp_path):
    settings = _settings(tmp_path)
    assert not settings.config_path.exists()
    assert not settings.env_path.exists()

    result = bootstrap_phase0(settings)
    assert result.created_goals is True
    assert result.created_env is True
    assert result.migrated_db is True

    # goals.yaml is a VALID GoalsConfig (read_config validates it).
    assert settings.config_path.exists()
    config = read_config(settings.config_path)
    assert config.research_goals  # non-empty placeholder goals
    assert config.relevance_scale  # required field present

    # The triage DB was created by the migration.
    assert settings.triage_db_path.exists()


def test_env_skeleton_has_no_real_secret(tmp_path):
    settings = _settings(tmp_path)
    bootstrap_phase0(settings)
    env_text = settings.env_path.read_text(encoding="utf-8")
    # The secret placeholder is COMMENTED — no live secret assignment.
    assert "# OPENAI_API_KEY=" in env_text
    # No uncommented OPENAI_API_KEY=<value> line.
    for line in env_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("OPENAI_API_KEY="):
            raise AssertionError(f"env skeleton must not set a live key: {stripped!r}")
    # The empty path keys are present for the setup flow to fill.
    assert "PDF_ROOT=" in env_text
    assert "ZOTERO_DATA_DIR=" in env_text


def test_present_files_are_not_overwritten(tmp_path):
    settings = _settings(tmp_path)
    # Pre-create both files with sentinel content.
    settings.config_path.parent.mkdir(parents=True, exist_ok=True)
    settings.config_path.write_text("research_goals: [keep-me]\n", encoding="utf-8")
    settings.env_path.write_text("OPENAI_API_KEY=sk-existing\n", encoding="utf-8")

    result = bootstrap_phase0(settings)
    assert result.created_goals is False
    assert result.created_env is False
    # Content is byte-for-byte unchanged.
    assert settings.config_path.read_text(encoding="utf-8") == "research_goals: [keep-me]\n"
    assert settings.env_path.read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-existing\n"


def test_idempotent_second_run_is_noop(tmp_path):
    settings = _settings(tmp_path)
    first = bootstrap_phase0(settings)
    assert first.created_goals and first.created_env and first.migrated_db

    second = bootstrap_phase0(settings)
    assert second.created_goals is False
    assert second.created_env is False
    assert second.migrated_db is False


def test_default_goals_validate_as_goalsconfig(tmp_path):
    """The generated default must round-trip through the real config reader (it's
    the same validation startup uses)."""
    settings = _settings(tmp_path)
    bootstrap_phase0(settings)
    config = read_config(settings.config_path)
    # llm_routing is synthesized from the llm block by GoalsConfig's validator.
    assert config.llm_routing is not None
    assert config.llm_routing.default.provider
