"""Phase 0 bootstrap: make a fresh checkout runnable with no manual steps.

Consolidates the old "copy the example templates + run migrate" ritual into one
idempotent, safe operation invoked from the ``serve`` startup path:

  * ``goals.yaml`` absent  → write a valid default config (``GoalsConfig`` defaults
    + placeholder research goals the user edits in Settings).
  * ``.env`` absent        → write a minimal skeleton: a COMMENTED secret
    placeholder (never a real key) + empty ``PDF_ROOT`` / ``ZOTERO_DATA_DIR`` lines.
  * triage DB absent       → run the existing version-gated migration logic.

Every step is guarded: an existing file is NEVER overwritten. Re-running is a
no-op. This is the single sanctioned writer of ``goals.yaml`` / ``.env`` at boot.
"""
from __future__ import annotations

from dataclasses import dataclass

from zotero_summarizer.models.config import (
    GoalsConfig,
    LLMConfig,
)
from zotero_summarizer.services._common import LOGGER, write_config_atomic
from zotero_summarizer.settings import Settings


# A minimal, VALID GoalsConfig needs these required fields filled. We use neutral
# placeholders the user replaces in Settings — never invented research content.
_DEFAULT_RESEARCH_GOALS = [
    "Replace with your first research focus area",
    "Replace with your second research focus area",
    "Replace with your third research focus area",
]
_DEFAULT_TRIAGE_CRITERIA = [
    "Relevance to your stated research goals above.",
    "Presence of a robust evaluation protocol (external validation, ablation, CIs).",
    "Clear methodology details (dataset provenance, preprocessing, training setup).",
]
_DEFAULT_RELEVANCE_SCALE = {
    1: "Tangential topic, weak methods, low practical utility for current goals.",
    2: "Some overlap with goals but limited methodological rigor or novelty.",
    3: "Moderately relevant, useful signals, but incomplete evidence for adoption.",
    4: "Highly relevant with strong methods and likely near-term impact.",
    5: "Critical relevance, excellent rigor, immediate applicability.",
}
_DEFAULT_SUMMARY_STRUCTURE = [
    "Executive Summary",
    "Should I Deep Read This?",
    "Key Sections to Read",
    "Relevance to My Research",
    "Key Findings",
    "Methods",
    "Limitations",
]

# Minimal .env skeleton. The secret placeholder is COMMENTED so the app never
# boots with a bogus key; PDF_ROOT / ZOTERO_DATA_DIR are empty for the setup UI
# (or `zotero-summarizer setup`) to fill via the allowlisted env writer.
_ENV_SKELETON = """\
# Zotero-summarizer environment. Secrets + filesystem paths live here.
# Fill these in via the Settings UI, `zotero-summarizer setup`, or by editing
# this file directly. This file is gitignored — never commit real secrets.

# LLM provider key. Uncomment and set the value (or point your provider profile
# in goals.yaml at a different api_key_env). NEVER commit a real key.
# OPENAI_API_KEY=

# Filesystem paths (written by the setup flow; both may be left blank to use the
# defaults — your home dir for PDF_ROOT, ~/Zotero for ZOTERO_DATA_DIR).
PDF_ROOT=
ZOTERO_DATA_DIR=
"""


@dataclass(frozen=True)
class BootstrapResult:
    """What the bootstrap actually did (each flag True only when it created the
    file/ran the step on THIS call; False = already present / no-op)."""

    created_goals: bool
    created_env: bool
    migrated_db: bool


def _default_goals_config() -> GoalsConfig:
    """A valid default ``GoalsConfig`` from defaults + neutral placeholders.

    ``GoalsConfig()`` cannot be built bare — ``llm`` and ``relevance_scale`` are
    required and the list validators reject empty research_goals/triage_criteria/
    summary_structure. We supply the minimum valid set; everything else (corpus,
    prestige, classifier_gate, …) takes its model default. ``llm_routing`` is
    synthesized from the ``llm`` block by ``GoalsConfig``'s model validator.
    """
    return GoalsConfig(
        research_goals=list(_DEFAULT_RESEARCH_GOALS),
        triage_criteria=list(_DEFAULT_TRIAGE_CRITERIA),
        relevance_scale=dict(_DEFAULT_RELEVANCE_SCALE),
        summary_structure=list(_DEFAULT_SUMMARY_STRUCTURE),
        llm=LLMConfig(
            draft_model="gpt-oss:20b",
            refine_model="gpt-oss:20b",
            api_base="http://localhost:11434/v1",
            api_key_env="OPENAI_API_KEY",
        ),
    )


def _bootstrap_goals(settings: Settings) -> bool:
    """Create goals.yaml from defaults when absent. Returns True iff written."""
    config_path = settings.config_path
    if config_path.exists():
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _default_goals_config().model_dump(mode="json")
    write_config_atomic(config_path, payload)
    LOGGER.info("Bootstrap: created default config at %s (edit research goals in Settings)", config_path)
    return True


def _bootstrap_env(settings: Settings) -> bool:
    """Create a minimal .env skeleton when absent. Returns True iff written."""
    env_path = settings.env_path
    if env_path.exists():
        return False
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(_ENV_SKELETON, encoding="utf-8")
    LOGGER.info("Bootstrap: created .env skeleton at %s (add your provider key + paths)", env_path)
    return True


def _bootstrap_database(settings: Settings) -> bool:
    """Run the version-gated migration when the triage DB is absent.

    Reuses ``storage.migrations.migrate_existing`` (the SAME logic behind the
    ``migrate`` CLI) — never a duplicate schema path. Returns True iff the DB was
    absent and got initialized on this call.
    """
    if settings.triage_db_path.exists():
        return False
    from zotero_summarizer.storage.migrations import migrate_existing

    migrate_existing(settings)
    LOGGER.info("Bootstrap: initialized triage DB at %s", settings.triage_db_path)
    return True


def bootstrap_phase0(settings: Settings) -> BootstrapResult:
    """Idempotently create goals.yaml + .env (when absent) and init the DB.

    Safe to call on every boot: present files are left untouched. Returns a
    ``BootstrapResult`` recording which steps actually ran this call.
    """
    created_goals = _bootstrap_goals(settings)
    created_env = _bootstrap_env(settings)
    migrated_db = _bootstrap_database(settings)
    return BootstrapResult(
        created_goals=created_goals,
        created_env=created_env,
        migrated_db=migrated_db,
    )
