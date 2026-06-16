"""Regression tests for the demanding-researcher UI audit fixes.

Each pins a real rough edge the audit surfaced:
  * refresh-labels 500 — goldenset called a removed ``note_analyzer._strip_html``
  * llm-check hang — a slow/unreachable provider had no per-probe timeout
  * paper-open latency — interactive scoring blocked on an OpenAlex network search
"""
from __future__ import annotations

import asyncio
import time

from zotero_summarizer.services.golden import goldenset
from zotero_summarizer.services.llm import operational_check as oc
from zotero_summarizer.storage import repositories as repo


# --- refresh-labels 500: note HTML stripping no longer AttributeErrors -------

def test_count_user_notes_strips_html_without_attributeerror():
    """goldenset._count_user_notes used note_analyzer._strip_html, removed in the
    services/ reorg → every Refresh-labels 500'd. It must strip tags via
    html_to_text and count a plain user note."""
    previews = ["<p>This paper <b>nails</b> the multi-agent eval. I'll apply this.</p>"]
    assert goldenset._count_user_notes(previews) == 1


def test_count_user_notes_excludes_pdf_annotation_dump():
    # A Zotero PDF-annotation export is not a user opinion → excluded.
    previews = ["Annotations (11/02/2024, 14:03) — highlight on page 3"]
    assert goldenset._count_user_notes(previews) == 0


def test_list_label_verdict_keys_exported_and_uncapped(tmp_path):
    """The golden-CSV re-export preserves manual verdicts via
    repositories.list_label_verdict_keys — it must be reachable on the
    `repositories` facade (the __all__ star-export) and return ALL keys with no
    5000-row cap (the bug chain that 500'd Refresh-labels)."""
    assert hasattr(repo, "list_label_verdict_keys"), "must be exported via repositories.__all__"
    db = tmp_path / "triage.db"
    conn = __import__("sqlite3").connect(str(db))
    repo.apply_schema(conn)
    conn.commit()
    conn.close()
    for i in range(7):
        repo.insert_or_update_label_verdict(
            db, item_key=f"feed:{i}", original_derived_priority="could_read",
            user_priority="should_read", comment="",
        )
    keys = repo.list_label_verdict_keys(db)
    assert keys == {f"feed:{i}" for i in range(7)}


def test_list_label_verdict_priorities_returns_latest_priority_per_key(tmp_path):
    """The reading-queue handled-filter needs the PRIORITY, not just the key, so
    a positive label stays visible while only dont_read hides. The reader returns
    one row per item_key (the UPSERT), reflecting the latest relabel."""
    assert hasattr(repo, "list_label_verdict_priorities"), "must be exported via repositories.__all__"
    db = tmp_path / "triage.db"
    conn = __import__("sqlite3").connect(str(db))
    repo.apply_schema(conn)
    conn.commit()
    conn.close()
    repo.insert_or_update_label_verdict(
        db, item_key="K1", original_derived_priority="could_read",
        user_priority="must_read", comment="",
    )
    repo.insert_or_update_label_verdict(
        db, item_key="K2", original_derived_priority="should_read",
        user_priority="dont_read", comment="",
    )
    # Relabel K1 — the UPSERT keeps one row, so the latest priority wins.
    repo.insert_or_update_label_verdict(
        db, item_key="K1", original_derived_priority="could_read",
        user_priority="should_read", comment="",
    )
    assert repo.list_label_verdict_priorities(db) == {"K1": "should_read", "K2": "dont_read"}


# --- llm-check: a slow probe times out per-stage instead of hanging ----------

def test_probe_stage_bounded_times_out(monkeypatch):
    monkeypatch.setattr(oc, "_PROBE_TIMEOUT_SECS", 0.05)
    monkeypatch.setattr(
        oc, "_stage_skeleton",
        lambda routing, stage: (None, {"stage": stage, "provider": "p", "type": "openai", "model": "m"}),
    )

    def _slow(routing, stage):  # simulates a hung/loading provider
        time.sleep(1.0)
        return {"stage": stage, "status": "operational", "detail": ""}

    monkeypatch.setattr(oc, "_probe_stage", _slow)
    row = asyncio.run(oc._probe_stage_bounded(routing=None, stage="feed"))
    assert row["status"] == "fail"
    assert "timeout" in row["detail"].lower()
    assert row["stage"] == "feed"


def test_probe_stage_bounded_passes_through_fast_result(monkeypatch):
    monkeypatch.setattr(oc, "_PROBE_TIMEOUT_SECS", 1.0)
    monkeypatch.setattr(
        oc, "_probe_stage",
        lambda routing, stage: {"stage": stage, "status": "operational", "detail": ""},
    )
    row = asyncio.run(oc._probe_stage_bounded(routing=None, stage="feed"))
    assert row["status"] == "operational"
