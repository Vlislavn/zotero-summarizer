from __future__ import annotations

import sqlite3

from zotero_summarizer.settings import Settings
from zotero_summarizer.services._common import read_config
from zotero_summarizer.storage.migrations import migrate_existing


def test_settings_loads_from_project_root_env_file(monkeypatch, tmp_path):
    for key in [
        "SUMMARY_TIMEOUT_SECONDS",
        "TRIAGE_JOB_CONCURRENCY",
        "APP_LOG_FILE",
        "ZOTERO_DATA_DIR",
    ]:
        monkeypatch.delenv(key, raising=False)

    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "SUMMARY_TIMEOUT_SECONDS=123",
                "TRIAGE_JOB_CONCURRENCY=99",
                "APP_LOG_FILE=logs/app.log",
                f"ZOTERO_DATA_DIR={tmp_path / 'Zotero'}",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.load(project_root=tmp_path)

    assert settings.project_root == tmp_path
    assert settings.summary_timeout_seconds == 123
    assert settings.triage_job_concurrency == 16
    assert settings.app_log_file == tmp_path / "logs/app.log"
    assert settings.triage_db_path == tmp_path / "triage_history.db"
    assert settings.corpus_db_path == tmp_path / "corpus_cache.db"


def test_goals_config_expands_llm_api_base_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    config_path = tmp_path / "goals.yaml"
    config_path.write_text(
        """
research_goals:
  - Goal
triage_criteria:
  - Criterion
relevance_scale:
  1: Low
  2: Some
  3: Medium
  4: High
  5: Critical
summary_structure:
  - Summary
llm:
  draft_model: test-model
  refine_model: test-model
  api_base: ${OPENAI_API_BASE}
  api_key_env: OPENAI_API_KEY
""",
        encoding="utf-8",
    )

    config = read_config(config_path)

    assert config.llm.api_base == "https://api.openai.com/v1"


def test_migrate_existing_initializes_both_databases(tmp_path):
    settings = Settings.load(project_root=tmp_path)

    result = migrate_existing(settings)

    assert result.triage_db_path.exists()
    assert result.corpus_db_path.exists()

    for db_path, namespace in [
        (result.triage_db_path, "triage"),
        (result.corpus_db_path, "corpus"),
    ]:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT version FROM schema_migrations WHERE namespace = ?",
                (namespace,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert int(row[0]) == result.schema_version
