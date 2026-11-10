"""Shared Telegram v2 test fixtures (the retired saved-news suite was removed).

Kept only for `_project`, imported by the live Telegram source/review tests.
"""
from __future__ import annotations

import json

from zotero_summarizer.settings import Settings
from zotero_summarizer.storage import migrations


INTERESTS = ("agent checkpoints", "биомедицинские агенты", "evaluation harness")


def _project(root):
    settings = Settings.load(project_root=root)
    migrations.run_migrations(settings.triage_db_path, "triage", migrations.TRIAGE_MIGRATIONS)
    profile = settings.data_dir / "research_feed" / "profile.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps({
        "schema_version": 1, "themes": list(INTERESTS[:2]), "projects": [INTERESTS[2]],
        "shortlist_budget": 4, "card_budget": 1, "telegram_channels": [],
    }, ensure_ascii=False), encoding="utf-8")
    provider = {"name": "news-default", "base_url": "https://provider.example.test/v1",
                "api_key_env": "FIXTURE_API_KEY", "max_tokens": 256}
    other = {**provider, "name": "unused-feed"}
    settings.config_path.write_text(json.dumps({
        "research_goals": ["saved news"], "triage_criteria": ["source-grounded relevance"],
        "relevance_scale": {str(i): f"fixture band {i}" for i in range(1, 6)},
        "summary_structure": ["summary"],
        "llm": {"draft_model": "unused-legacy", "refine_model": "unused-legacy",
                "api_base": "https://legacy.example.test/v1", "api_key_env": "FIXTURE_API_KEY"},
        "llm_routing": {"providers": [provider, other],
                        "default": {"provider": "news-default", "model": "fixture-news"},
                        "feed": {"provider": "unused-feed", "model": "must-not-use"}},
    }), encoding="utf-8")
    return settings
