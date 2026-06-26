"""Content-level duplicate protection (DOI/arXiv) across the two layers.

The identity dedup (``filter_unprocessed`` / ``dedup_keep_newest``) only ever
catches the *same* RSS item / GUID. A paper that re-arrives under a different
GUID — or that the user already trashed/added, or that already lives in the
library — used to sail straight back onto Today. These tests pin the two guards
that close that gap:

  * Layer 1 (triage): :func:`dedup_against_processed` rejects an incoming feed
    item whose DOI/arXiv already exists in ``processed_feed_items``.
  * Layer 2 (slate): :func:`assemble_daily_slate` / :func:`count_awaiting_unhandled`
    drop an awaiting card that duplicates a decided / in-library paper, and
    collapse same-paper copies that arrived under different GUIDs.

Matching is DOI/arXiv-only by design: a row with neither id is never dropped, so
a genuinely distinct paper can never be filtered out by mistake.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zotero_summarizer.services.triage.daily_select import (
    assemble_daily_slate,
    count_awaiting_unhandled,
)
from zotero_summarizer.storage import feeds as feeds_storage
from tests._daily_select_helpers import _DEFAULT_NOW, _create_db, _insert


@pytest.fixture
def triage_db(tmp_path: Path) -> Path:
    db = tmp_path / "triage.db"
    _create_db(db)
    return db


def _slate_keys(db: Path) -> list[str]:
    slate = assemble_daily_slate(db_path=db, K=5, now=_DEFAULT_NOW)
    return [p.item_key for p in slate.papers]


# ---------------------------------------------------------------------------
# Layer 2 — slate guard: drop awaiting cards that duplicate a decided/in-library paper
# ---------------------------------------------------------------------------


def test_slate_drops_awaiting_dupe_of_user_rejected(triage_db: Path) -> None:
    """A paper the user trashed must not reappear under a new GUID."""
    _insert(triage_db, item_key="rejected-guid", decision="user_rejected",
            composite_score=4.0, doi="10.1/paperA")
    _insert(triage_db, item_key="new-guid", decision="awaiting_review",
            composite_score=4.5, doi="10.1/paperA")
    assert _slate_keys(triage_db) == []
    assert count_awaiting_unhandled(triage_db) == 0


def test_slate_drops_awaiting_dupe_of_selected_by_arxiv(triage_db: Path) -> None:
    """A paper already kept into the library (matched by arXiv) drops out."""
    _insert(triage_db, item_key="kept-guid", decision="selected",
            composite_score=4.0, arxiv_id="2401.01234")
    _insert(triage_db, item_key="awaiting-guid", decision="awaiting_review",
            composite_score=4.5, arxiv_id="2401.01234")
    assert _slate_keys(triage_db) == []


def test_slate_drops_awaiting_dupe_of_materialized_row(triage_db: Path) -> None:
    """A row added to Zotero (materialized_zotero_key set) blocks its twin even
    when its own decision is still triaged_pending."""
    _insert(triage_db, item_key="mat-guid", decision="triaged_pending",
            composite_score=4.0, doi="10.5/added", materialized_zotero_key="ZKEY123")
    _insert(triage_db, item_key="awaiting-guid", decision="awaiting_review",
            composite_score=4.5, doi="10.5/added")
    assert _slate_keys(triage_db) == []


def test_slate_collapses_same_doi_awaiting_copies_keep_newest(triage_db: Path) -> None:
    """Two awaiting copies of one paper (different GUIDs) collapse to one."""
    older = _DEFAULT_NOW.replace(hour=8)
    newer = _DEFAULT_NOW.replace(hour=11)
    _insert(triage_db, item_key="copy-old", decision="awaiting_review",
            composite_score=4.0, doi="10.9/dup", created_at=older)
    _insert(triage_db, item_key="copy-new", decision="awaiting_review",
            composite_score=4.0, doi="10.9/dup", created_at=newer)
    keys = _slate_keys(triage_db)
    assert keys == ["copy-new"]
    assert count_awaiting_unhandled(triage_db) == 1


def test_slate_keeps_distinct_paper_no_false_positive(triage_db: Path) -> None:
    """A different DOI is never dropped — the guard must not over-filter."""
    _insert(triage_db, item_key="rejected-guid", decision="user_rejected",
            composite_score=4.0, doi="10.1/paperA")
    _insert(triage_db, item_key="distinct-guid", decision="awaiting_review",
            composite_score=4.5, doi="10.1/paperB")
    assert _slate_keys(triage_db) == ["distinct-guid"]


def test_slate_keeps_no_identifier_card_even_with_title_collision(triage_db: Path) -> None:
    """DOI/arXiv-only: a card with no DOI/arXiv is never dropped, even if a
    decided row shares its title (we deliberately don't match on title)."""
    _insert(triage_db, item_key="rej-guid", decision="user_rejected",
            composite_score=4.0, title="Same Title", doi="10.1/has-doi")
    _insert(triage_db, item_key="await-guid", decision="awaiting_review",
            composite_score=4.5, title="Same Title")  # no doi/arxiv
    assert _slate_keys(triage_db) == ["await-guid"]


def test_slate_doi_match_is_prefix_insensitive(triage_db: Path) -> None:
    """A blocked DOI stored as a URL matches an awaiting bare DOI (normalize_doi)."""
    _insert(triage_db, item_key="rej-guid", decision="user_rejected",
            composite_score=4.0, doi="https://doi.org/10.2/VariantDOI")
    _insert(triage_db, item_key="await-guid", decision="awaiting_review",
            composite_score=4.5, doi="10.2/variantdoi")
    assert _slate_keys(triage_db) == []


def test_slate_arxiv_match_ignores_version(triage_db: Path) -> None:
    """A newer arXiv version is the same paper for dedup."""
    _insert(triage_db, item_key="rej-guid", decision="user_rejected",
            composite_score=4.0, arxiv_id="2401.01234v1")
    _insert(triage_db, item_key="await-guid", decision="awaiting_review",
            composite_score=4.5, arxiv_id="2401.01234v2")
    assert _slate_keys(triage_db) == []


def test_slate_drops_no_id_card_re_arriving_under_trashed_guid(triage_db: Path) -> None:
    """THE durable-trash fix: a paper with no DOI/arXiv that the user trashed,
    re-arriving under the *same* stable GUID but a fresh feed_item_id, stays gone.

    DOI/arXiv content-dedup can't see it (no ids) and the per-item label/decision
    sit on the old feed_item_id — only the GUID survives, so it is the key."""
    _insert(triage_db, item_key="same-guid", decision="user_rejected",
            composite_score=4.0)  # no doi/arxiv, original trashed copy
    _insert(triage_db, item_key="same-guid", decision="awaiting_review",
            composite_score=4.5)  # same guid, new feed_item_id, still no ids
    assert _slate_keys(triage_db) == []
    assert count_awaiting_unhandled(triage_db) == 0


def test_slate_drops_no_id_card_trashed_in_zotero_by_guid(triage_db: Path) -> None:
    """A paper thrown away inside Zotero (final_outcome=trashed) blocks its
    no-id re-arrival by GUID, even though its own decision was ``selected``."""
    _insert(triage_db, item_key="zot-guid", decision="selected",
            composite_score=4.0, final_outcome="trashed")
    _insert(triage_db, item_key="zot-guid", decision="awaiting_review",
            composite_score=4.5)
    assert _slate_keys(triage_db) == []


def test_slate_keeps_no_id_card_with_distinct_guid(triage_db: Path) -> None:
    """No false positive: a no-id awaiting card whose GUID was never trashed is
    kept — GUID suppression must match exactly, never over-filter."""
    _insert(triage_db, item_key="trashed-guid", decision="user_rejected",
            composite_score=4.0)  # no ids
    _insert(triage_db, item_key="other-guid", decision="awaiting_review",
            composite_score=4.5)  # different guid, no ids
    assert _slate_keys(triage_db) == ["other-guid"]


# ---------------------------------------------------------------------------
# Layer 1 — triage-time content dedup
# ---------------------------------------------------------------------------


def test_partition_by_content_splits_dupes_and_keepers() -> None:
    from zotero_summarizer.services.triage.feeds._tick_dedup import _partition_by_content

    items = [
        {"doi": "10.1/known"},          # dupe (DOI already seen)
        {"arxiv_id": "2401.01234v3"},   # dupe (arXiv already seen, version differs)
        {"doi": "10.1/fresh"},          # keep
        {"title": "no ids"},            # keep (no identifier → never a dupe)
    ]
    keep, dups = _partition_by_content(
        items, seen_doi={"10.1/known"}, seen_arxiv={"2401.01234"},
    )
    assert {d.get("doi") or d.get("arxiv_id") for d in dups} == {"10.1/known", "2401.01234v3"}
    assert len(keep) == 2


def test_partition_dedups_within_the_same_batch() -> None:
    from zotero_summarizer.services.triage.feeds._tick_dedup import _partition_by_content

    items = [{"doi": "10.7/batchdup"}, {"doi": "10.7/batchdup"}]
    keep, dups = _partition_by_content(items, seen_doi=set(), seen_arxiv=set())
    assert len(keep) == 1
    assert len(dups) == 1


def test_fetch_processed_content_pairs_excludes_errors(triage_db: Path) -> None:
    import sqlite3

    _insert(triage_db, item_key="g1", decision="awaiting_review",
            composite_score=4.0, doi="10.1/kept")
    _insert(triage_db, item_key="g2", decision="skipped_error",
            composite_score=0.0, doi="10.1/errored")
    _insert(triage_db, item_key="g3", decision="user_rejected",
            composite_score=4.0, arxiv_id="2401.55555")
    conn = sqlite3.connect(str(triage_db))
    conn.row_factory = sqlite3.Row
    try:
        pairs = feeds_storage.fetch_processed_content_pairs(
            conn, exclude_decisions=("skipped_error",),
        )
    finally:
        conn.close()
    dois = {d for d, _ in pairs}
    arxivs = {a for _, a in pairs}
    assert "10.1/kept" in dois
    assert "2401.55555" in arxivs
    assert "10.1/errored" not in dois  # retryable, excluded


def test_dedup_against_processed_rejects_known_doi(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: an incoming item whose DOI is already processed is split out."""
    from zotero_summarizer.services.triage.feeds import _common, _tick_dedup

    db = tmp_path / "triage.db"
    _create_db(db)
    _insert(db, item_key="seen", decision="user_rejected",
            composite_score=4.0, doi="10.3/already")

    fake_settings = SimpleNamespace(triage_db_path=db)
    monkeypatch.setattr(_common, "get_settings", lambda: fake_settings)

    incoming = [
        {"feed_library_id": 1, "item_id": 900, "title": "dupe", "doi": "10.3/already"},
        {"feed_library_id": 1, "item_id": 901, "title": "fresh", "doi": "10.3/new"},
    ]
    to_triage, dups = _tick_dedup.dedup_against_processed(
        incoming, tick_id="t", enabled=True,
    )
    assert [i["item_id"] for i in to_triage] == [901]
    assert [i["item_id"] for i in dups] == [900]


def test_dedup_against_processed_disabled_is_passthrough(tmp_path: Path, monkeypatch) -> None:
    """``enabled=False`` switches off only the DOI/arXiv content guard; with
    nothing trashed the call is a passthrough (trash suppression below stays on)."""
    from zotero_summarizer.services.triage.feeds import _common, _tick_dedup

    db = tmp_path / "triage.db"
    _create_db(db)
    fake_settings = SimpleNamespace(triage_db_path=db)
    monkeypatch.setattr(_common, "get_settings", lambda: fake_settings)

    incoming = [{"feed_library_id": 1, "item_id": 1, "guid": "g", "doi": "10.1/x"}]
    to_triage, dups = _tick_dedup.dedup_against_processed(
        incoming, tick_id="t", enabled=False,
    )
    assert to_triage == incoming
    assert dups == []


def test_dedup_against_processed_rejects_trashed_guid_even_when_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    """Trash suppression is ALWAYS on: a re-arrival under the same stable GUID as
    a paper the user trashed is rejected even with content-dedup off and no DOI —
    the durable "trash → never show again" guard for id-less journal/news items."""
    from zotero_summarizer.services.triage.feeds import _common, _tick_dedup

    db = tmp_path / "triage.db"
    _create_db(db)
    # A no-id paper trashed from Today (guid == the feed item's stable URL).
    _insert(db, item_key="trashed-url", decision="user_rejected", composite_score=4.0)

    fake_settings = SimpleNamespace(triage_db_path=db)
    monkeypatch.setattr(_common, "get_settings", lambda: fake_settings)

    incoming = [
        {"feed_library_id": 1, "item_id": 950, "guid": "trashed-url", "title": "re-arrival"},
        {"feed_library_id": 1, "item_id": 951, "guid": "fresh-url", "title": "fresh"},
    ]
    to_triage, dups = _tick_dedup.dedup_against_processed(
        incoming, tick_id="t", enabled=False,
    )
    assert [i["item_id"] for i in to_triage] == [951]
    assert [i["item_id"] for i in dups] == [950]


def test_dedup_against_processed_rejects_guid_trashed_in_zotero(
    tmp_path: Path, monkeypatch
) -> None:
    """A paper thrown away inside Zotero (final_outcome=trashed) suppresses its
    re-arrival by GUID just like a Today-trash does."""
    from zotero_summarizer.services.triage.feeds import _common, _tick_dedup

    db = tmp_path / "triage.db"
    _create_db(db)
    _insert(db, item_key="zot-url", decision="selected", composite_score=4.0,
            final_outcome="trashed")

    fake_settings = SimpleNamespace(triage_db_path=db)
    monkeypatch.setattr(_common, "get_settings", lambda: fake_settings)

    incoming = [{"feed_library_id": 1, "item_id": 960, "guid": "zot-url", "title": "back again"}]
    to_triage, dups = _tick_dedup.dedup_against_processed(
        incoming, tick_id="t", enabled=True,
    )
    assert to_triage == []
    assert [i["item_id"] for i in dups] == [960]
