from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def default_project_root() -> Path:
    configured = os.getenv("ZOTERO_SUMMARIZER_HOME")
    if configured:
        return Path(configured).expanduser().resolve()

    cwd = Path.cwd().resolve()
    if (cwd / "goals.yaml").exists():
        return cwd

    # In an editable checkout this resolves to the repository root.
    return Path(__file__).resolve().parents[1]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    project_root: Path
    # All app-generated state (SQLite DBs, golden dataset, logs, eval/run
    # artifacts) lives under ``data_dir`` so the project root stays clean.
    data_dir: Path
    config_path: Path
    env_path: Path
    summary_timeout_seconds: int
    triage_job_concurrency: int
    pdf_root: Path
    zotero_data_dir: Path
    app_log_level: str
    app_log_file: Path
    triage_db_path: Path
    corpus_db_path: Path
    golden_csv_path: Path
    golden_jsonl_path: Path
    faithbench_dir: Path
    paper_render_dir: Path
    # App-owned browser profile for university institutional access (persistent
    # login session). Default under data/; overridable via UniversityAccessConfig.
    browser_profile_dir: Path

    @classmethod
    def load(
        cls,
        *,
        project_root: str | Path | None = None,
        config_path: str | Path | None = None,
        env_path: str | Path | None = None,
    ) -> "Settings":
        root = Path(project_root).expanduser().resolve() if project_root else default_project_root()
        data_dir = root / "data"
        env_file = Path(env_path).expanduser().resolve() if env_path else root / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=True)

        config_file = Path(config_path).expanduser().resolve() if config_path else root / "goals.yaml"
        configured_log_file = Path(os.getenv("APP_LOG_FILE", "server.log")).expanduser()
        app_log_file = configured_log_file if configured_log_file.is_absolute() else data_dir / configured_log_file

        concurrency = max(1, min(_env_int("TRIAGE_JOB_CONCURRENCY", 4), 16))

        return cls(
            project_root=root,
            data_dir=data_dir,
            config_path=config_file,
            env_path=env_file,
            summary_timeout_seconds=_env_int("SUMMARY_TIMEOUT_SECONDS", 420),
            triage_job_concurrency=concurrency,
            pdf_root=Path(os.getenv("PDF_ROOT", str(Path.home()))).expanduser().resolve(),
            zotero_data_dir=Path(os.getenv("ZOTERO_DATA_DIR", str(Path.home() / "Zotero"))).expanduser().resolve(),
            app_log_level=os.getenv("APP_LOG_LEVEL", "INFO").upper(),
            app_log_file=app_log_file,
            triage_db_path=data_dir / "triage_history.db",
            corpus_db_path=data_dir / "corpus_cache.db",
            golden_csv_path=data_dir / "zotero-summarizer-golden.csv",
            golden_jsonl_path=data_dir / "zotero-summarizer-golden.jsonl",
            faithbench_dir=data_dir / "faithbench",
            paper_render_dir=data_dir / "paper_render",
            browser_profile_dir=data_dir / "browser_profile",
        )
