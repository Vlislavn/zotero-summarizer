from __future__ import annotations

import math
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


def offline_requested() -> bool:
    explicit = (os.getenv("ZS_OFFLINE") or "").strip().lower()
    return explicit in {"1", "true", "yes", "on"} or os.getenv("HF_HUB_OFFLINE") == "1"


def automatic_gate_updates_enabled() -> bool:
    """Process-level permission for automatic classifier training/rescoring."""
    raw = os.getenv("ZS_AUTOMATIC_GATE_UPDATES", "1").strip().lower()
    if raw not in {"true", "false", "1", "0"}:
        raise ValueError("ZS_AUTOMATIC_GATE_UPDATES must be true/false or 1/0")
    return raw in {"true", "1"}


def today_exposure_log_enabled() -> bool:
    """Owner-approved local logging of what Today shows and does (default on)."""
    raw = os.getenv("ZS_TODAY_EXPOSURE_LOG", "1").strip().lower()
    if raw not in {"true", "false", "1", "0"}:
        raise ValueError("ZS_TODAY_EXPOSURE_LOG must be true/false or 1/0")
    return raw in {"true", "1"}


def today_exploration_slots() -> int:
    """Random below-the-bar Today cards per local day: 0..2 (default 2); 0 when logging is off."""
    raw = os.getenv("ZS_TODAY_EXPLORATION_SLOTS", "2").strip()
    if raw not in {"0", "1", "2"}:
        raise ValueError("ZS_TODAY_EXPLORATION_SLOTS must be 0, 1 or 2")
    return int(raw) if today_exposure_log_enabled() else 0


def today_ranker() -> str:
    """Today order id, logged on every exposure row: ``goal_cos_v1`` (default) or ``composite_v1``.

    Operational setting; it is not part of review identity."""
    raw = os.getenv("ZS_TODAY_RANKER", "goal_cos_v1").strip()
    if raw not in {"goal_cos_v1", "composite_v1"}:
        raise ValueError("ZS_TODAY_RANKER must be goal_cos_v1 or composite_v1")
    return raw


def paper_auto_review_enabled() -> bool:
    """Opt-in: auto-start a deep review when a paper page opens (default OFF).

    Strict gate — the paper page only auto-generates when this is explicitly on;
    otherwise the owner presses *Generate deep review*. Operational setting; it is
    not part of review identity."""
    raw = os.getenv("ZS_PAPER_AUTO_REVIEW", "0").strip().lower()
    if raw not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise ValueError("ZS_PAPER_AUTO_REVIEW must be true/false or 1/0")
    return raw in {"true", "1", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _feedback_schedule_env():
    raw = os.getenv("ZS_FEEDBACK_SCHEDULE_ENABLED", "true").strip().lower()
    if raw not in {"true", "false", "1", "0"}:
        raise ValueError("ZS_FEEDBACK_SCHEDULE_ENABLED must be true/false or 1/0")
    return {"feedback_schedule_enabled": raw in {"true", "1"},
            "feedback_schedule_max_windows": int(os.getenv("ZS_FEEDBACK_SCHEDULE_MAX_WINDOWS", "2")),
            "feedback_schedule_wall_cap_seconds": float(os.getenv("ZS_FEEDBACK_SCHEDULE_WALL_CAP_SECONDS", "240"))}


@dataclass(frozen=True)
class Settings:
    project_root: Path
    # All app-generated state (SQLite DBs, golden dataset, logs, eval/run
    # artifacts) lives under ``data_dir`` so the project root stays clean.
    data_dir: Path
    config_path: Path
    # Per-user calibration artifact (Tier 1-3): system-owned knobs the app derived for
    # THIS setup (env-probe + opt-in sweep + data-driven recal). Precedence sits between
    # goals.yaml and ZS_* env. Derived, like the model joblib — never hand-authored.
    calibration_path: Path
    env_path: Path
    # Bounds each OpenAI-compatible HTTP request and the complete triage summary job.
    summary_timeout_seconds: int
    triage_job_concurrency: int
    pdf_root: Path
    zotero_data_dir: Path
    app_log_level: str
    app_log_file: Path
    triage_db_path: Path
    corpus_db_path: Path
    model_dir: Path
    tuned_params_path: Path
    pdf_cache_dir: Path
    golden_csv_path: Path
    golden_jsonl_path: Path
    # Append-only agentic interaction log (immutable human-decision trajectory).
    interaction_log_path: Path
    faithbench_dir: Path
    paper_render_dir: Path
    # Persisted Targeted Search research sessions (one JSON per session).
    search_dir: Path
    # App-owned browser profile for university institutional access (persistent
    # login session). Default under data/; overridable via UniversityAccessConfig.
    browser_profile_dir: Path
    setup_load_approval_policy_path: Path | None = None
    setup_load_approved_policy_sha256: str | None = None
    feedback_schedule_enabled: bool = True
    feedback_schedule_max_windows: int = 2
    feedback_schedule_wall_cap_seconds: float = 240.0

    def __post_init__(self):
        if type(self.feedback_schedule_enabled) is not bool:
            raise ValueError("feedback schedule enabled must be boolean")
        if type(self.feedback_schedule_max_windows) is not int or not 1 <= self.feedback_schedule_max_windows <= 4:
            raise ValueError("feedback schedule windows must be 1..4")
        wall = self.feedback_schedule_wall_cap_seconds
        if type(wall) not in (int, float) or not math.isfinite(wall) or not 0 < wall <= 600:
            raise ValueError("feedback schedule safety wall must be positive and <=600")

    @property
    def today_exposure_log_dir(self) -> Path:
        """Append-only Today exposure JSONL, one ``YYYY-MM-DD.jsonl`` per local day."""
        return self.data_dir / "today-exposure"

    @property
    def card_facts_dir(self) -> Path:
        """On-demand Today card facts cache, one JSON per stable paper key."""
        return self.data_dir / "card_facts"

    @property
    def card_dir(self) -> Path:
        """Today AI card store (cards/, consent, token ledger, breaker) — operational,
        not part of the review identity."""
        return self.data_dir / "card"

    @property
    def feedback_schedule_dir(self) -> Path:
        return self.feedback_analysis_dir / "schedule"

    @property
    def feedback_analysis_dir(self) -> Path:
        return self.data_dir / "feedback-analysis"

    @property
    def nli_model_dir(self) -> Path:
        """Local NLI model (mDeBERTa-v3 XNLI) that checks Telegram gist lines against their cited comments."""
        return self.data_dir / "models" / "nli-mdeberta"

    @property
    def classifier_runs_log_path(self) -> Path:
        return self.data_dir / "classifier-runs.jsonl"

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
            calibration_path=data_dir / "calibration.json",
            env_path=env_file,
            summary_timeout_seconds=_env_int("SUMMARY_TIMEOUT_SECONDS", 420),
            triage_job_concurrency=concurrency,
            pdf_root=Path(os.getenv("PDF_ROOT", str(Path.home()))).expanduser().resolve(),
            zotero_data_dir=Path(
                os.getenv("ZOTERO_DATA_DIR", "").strip() or str(Path.home() / "Zotero")
            ).expanduser().resolve(),
            app_log_level=os.getenv("APP_LOG_LEVEL", "INFO").upper(),
            app_log_file=app_log_file,
            triage_db_path=data_dir / "triage_history.db",
            corpus_db_path=data_dir / "corpus_cache.db",
            model_dir=data_dir / "models",
            tuned_params_path=data_dir / "optuna-best-params.json",
            pdf_cache_dir=data_dir / "pdfs",
            golden_csv_path=data_dir / "zotero-summarizer-golden.csv",
            golden_jsonl_path=data_dir / "zotero-summarizer-golden.jsonl",
            interaction_log_path=data_dir / "interaction-events.jsonl",
            faithbench_dir=data_dir / "faithbench",
            paper_render_dir=data_dir / "paper_render",
            search_dir=data_dir / "search",
            browser_profile_dir=data_dir / "browser_profile",
            setup_load_approval_policy_path=(Path(os.environ["ZS_SETUP_LOAD_APPROVAL_POLICY_PATH"])
                if "ZS_SETUP_LOAD_APPROVAL_POLICY_PATH" in os.environ else None),
            setup_load_approved_policy_sha256=os.getenv("ZS_SETUP_LOAD_APPROVED_POLICY_SHA256"),
            **_feedback_schedule_env(),
        )
