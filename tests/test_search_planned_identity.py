"""Authored Search reservation/replay controls; parent owns runtime qualification."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests._zotero_fixtures import add_library_item
from tests.test_search_materialize_recovery import _native_keys, _setup
from zotero_summarizer.services.search._models import Candidate
from zotero_summarizer.services.search.dedup import to_version_families


def _leave_native_commit_unfinalized(monkeypatch, session, materialize, candidate_id):
    save = session.save

    def fail_finalization(research):
        if research.candidates[0].materialized_zotero_key:
            raise OSError("synthetic finalization failure")
        save(research)

    with monkeypatch.context() as patch:
        patch.setattr(session, "save", fail_finalization)
        with pytest.raises(OSError, match="synthetic finalization failure"):
            materialize.materialize_candidate("recovery", candidate_id, None)
    pending = session.load("recovery").candidates[0]
    assert pending.planned_zotero_key == "ADDED001"
    assert pending.materialized_zotero_key == ""
    return pending


def _native_state(db_path):
    tables = ("items", "itemData", "itemDataValues", "creators", "itemCreators",
              "collectionItems", "itemTags", "itemNotes")
    with sqlite3.connect(db_path) as conn:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in tables}


@pytest.mark.parametrize("key,collection", [("ZX9A1234", None), ("77777777", "RESEARCH1")])
def test_plan_precedes_native_work_and_materialized_stamp_follows_return(monkeypatch, tmp_path, key, collection):
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter
    from zotero_summarizer.services.triage import feeds

    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(feeds, "_generate_zotero_key", lambda used: key)
    apply = ZoteroWriter.apply_feed_materialization
    calls = []

    def native(writer, **kwargs):
        current = session.load("recovery").candidates[0]
        assert current.planned_zotero_key == kwargs["new_item_key"] == key
        assert current.materialized_zotero_key == ""
        assert current.in_library is None and current.existing_zotero_key is None
        calls.append(key)
        return apply(writer, **kwargs)

    monkeypatch.setattr(ZoteroWriter, "apply_feed_materialization", native)
    result = materialize.materialize_candidate("recovery", candidate_id, collection)
    assert result["status"] == "added" and result["zotero_key"] == key
    current = session.load("recovery").candidates[0]
    assert current.planned_zotero_key == current.materialized_zotero_key == key
    assert calls == _native_keys(db_path) == [key]
    assert materialize.materialize_candidate("recovery", candidate_id, collection)["status"] == "already_added"
    assert calls == [key]


def test_native_transaction_failure_retries_the_persisted_identity(monkeypatch, tmp_path):
    from zotero_summarizer.services.triage import feeds

    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TRIGGER fail_note BEFORE INSERT ON itemNotes "
                     "BEGIN SELECT RAISE(ABORT, 'synthetic note failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic note failure"):
        materialize.materialize_candidate("recovery", candidate_id, None)
    pending = session.load("recovery").candidates[0]
    assert pending.planned_zotero_key == "ADDED001" and pending.materialized_zotero_key == ""
    assert _native_keys(db_path) == []
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER fail_note")
    monkeypatch.setattr(feeds, "_generate_zotero_key", lambda used: pytest.fail("retry generated a new key"))
    assert materialize.materialize_candidate("recovery", candidate_id, None)["zotero_key"] == "ADDED001"
    assert _native_keys(db_path) == ["ADDED001"]


@pytest.mark.parametrize("enrich", [False, True])
def test_stale_background_merge_preserves_the_saved_plan(monkeypatch, tmp_path, enrich):
    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    stale = session.load("recovery")
    _leave_native_commit_unfinalized(monkeypatch, session, materialize, candidate_id)
    if enrich:
        richer = Candidate(title="Richer journal metadata", doi=stale.candidates[0].doi,
                           abstract="An independently acquired longer abstract.", venue="Journal", pmid="991")
        stale.candidates = to_version_families(stale.candidates + [richer])
    stale.candidates[0].quality = {"grade": "B"}
    session.save_merge(stale)
    pending = session.load("recovery").candidates[0]
    assert pending.candidate_id == candidate_id
    assert pending.planned_zotero_key == "ADDED001" and pending.materialized_zotero_key == ""
    assert pending.in_library is None and pending.existing_zotero_key is None
    assert pending.quality == {"grade": "B"}
    if enrich:
        assert pending.title == "Richer journal metadata" and pending.pmid == "991"
    assert materialize.materialize_candidate("recovery", candidate_id, None)["zotero_key"] == "ADDED001"
    assert _native_keys(db_path) == ["ADDED001"]


def test_version_family_enrichment_keeps_plan_separate_from_native_presence():
    pending = Candidate(title="Preprint", arxiv_id="2501.12345", planned_zotero_key="PLAN1234")
    richer = Candidate(title="Journal article", arxiv_id=pending.arxiv_id, doi="10.1/enriched",
                       venue="Journal", abstract="Richer metadata")
    merged, = to_version_families([pending, richer])
    assert merged.candidate_id == pending.candidate_id and merged.title == "Journal article"
    assert merged.planned_zotero_key == "PLAN1234"
    assert merged.materialized_zotero_key == "" and merged.existing_zotero_key is None
    assert merged.in_library is None
    assert Candidate.from_dict(merged.to_dict()).planned_zotero_key == "PLAN1234"


def test_retry_after_native_commit_preserves_later_user_edits(monkeypatch, tmp_path):
    from zotero_summarizer.services.triage import feeds

    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    _leave_native_commit_unfinalized(monkeypatch, session, materialize, candidate_id)
    with sqlite3.connect(db_path) as conn:
        item_id = conn.execute("SELECT itemID FROM items WHERE key='ADDED001'").fetchone()[0]
        conn.execute("UPDATE items SET version=23, synced=1 WHERE itemID=?", (item_id,))
        conn.execute("UPDATE itemDataValues SET value='User edited title' WHERE valueID IN "
                     "(SELECT d.valueID FROM itemData d JOIN fields f USING(fieldID) "
                     "WHERE d.itemID=? AND f.fieldName='title')", (item_id,))
        conn.execute("UPDATE itemNotes SET note='<p>User edited note</p>' WHERE parentItemID=?", (item_id,))
        conn.execute("DELETE FROM collectionItems WHERE itemID=?", (item_id,))
        conn.execute("DELETE FROM itemTags WHERE itemID=?", (item_id,))
    before = _native_state(db_path)
    monkeypatch.setattr(feeds, "_generate_zotero_key", lambda used: pytest.fail("lost native identity"))
    materialize.materialize_candidate("recovery", candidate_id, None)
    assert _native_state(db_path) == before
    assert _native_keys(db_path) == ["ADDED001"]


@pytest.mark.parametrize("hit", ["materialized", "coverage"])
def test_saved_and_coverage_hits_do_not_plan_generate_or_construct_writer(monkeypatch, tmp_path, hit):
    from zotero_summarizer.services.triage import feeds

    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    add_library_item(db_path, item_key="KNOWN001", title="Already saved")
    research = session.load("recovery")
    if hit == "materialized":
        research.candidates[0].materialized_zotero_key = "KNOWN001"
    else:
        research.candidates[0].existing_zotero_key = "KNOWN001"
        research.candidates[0].in_library = True
    session.save(research)
    before = _native_state(db_path)
    monkeypatch.setattr(feeds, "_generate_zotero_key", lambda used: pytest.fail("unnecessary key generation"))
    monkeypatch.setattr(materialize, "ZoteroWriter", lambda *args: pytest.fail("unnecessary native writer"))
    assert materialize.materialize_candidate("recovery", candidate_id, None) == {
        "status": "already_added", "zotero_key": "KNOWN001",
    }
    current = session.load("recovery").candidates[0]
    assert current.planned_zotero_key == "" and "planned_zotero_key" not in current.to_dict()
    assert _native_state(db_path) == before


def test_simultaneous_adds_share_one_saved_plan_and_native_call(monkeypatch, tmp_path):
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter
    from zotero_summarizer.services.triage import feeds

    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    generated, dispatched = [], []
    keys = iter(("SAFE1234", "SAFE5678"))
    apply = ZoteroWriter.apply_feed_materialization
    start = Barrier(2)

    def generate(used):
        key = next(keys)
        generated.append(key)
        return key

    def native(writer, **kwargs):
        current = session.load("recovery").candidates[0]
        assert current.planned_zotero_key == kwargs["new_item_key"]
        assert current.materialized_zotero_key == ""
        dispatched.append(kwargs["new_item_key"])
        return apply(writer, **kwargs)

    def add():
        start.wait()
        return materialize.materialize_candidate("recovery", candidate_id, None)

    monkeypatch.setattr(feeds, "_generate_zotero_key", generate)
    monkeypatch.setattr(ZoteroWriter, "apply_feed_materialization", native)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(add) for _ in range(2)]
        results = [future.result() for future in futures]
    assert sorted(row["status"] for row in results) == ["added", "already_added"]
    assert generated == dispatched == _native_keys(db_path) == ["SAFE1234"]


def test_legacy_session_without_plan_keeps_its_wire_shape(monkeypatch, tmp_path):
    session, _materialize, _candidate_id, _db_path = _setup(monkeypatch, tmp_path)
    research = session.load("recovery")
    raw = research.to_dict()
    assert "planned_zotero_key" not in raw["candidates"][0]
    path = tmp_path / "sessions" / "recovery.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    restored = session.load("recovery")
    assert restored.candidates[0].planned_zotero_key == ""
    assert restored.to_dict() == raw
    session.save(restored)
    assert json.loads(path.read_text(encoding="utf-8")) == raw


@pytest.mark.parametrize("bad", [None, False, 0, [], {}, "short", "abcdefgh", "ABCDEFGH\n", "ＡBCDEFGH"])
def test_invalid_persisted_plan_is_rejected_without_native_work(monkeypatch, tmp_path, bad):
    from zotero_summarizer.services.triage import feeds

    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    raw = session.load("recovery").to_dict()
    raw["candidates"][0]["planned_zotero_key"] = bad
    path = tmp_path / "sessions" / "recovery.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(feeds, "_generate_zotero_key", lambda used: pytest.fail("replaced invalid plan"))
    monkeypatch.setattr(materialize, "ZoteroWriter", lambda *args: pytest.fail("invalid plan reached native work"))
    with pytest.raises(ValueError, match="Planned Zotero key"):
        materialize.materialize_candidate("recovery", candidate_id, None)
    assert path.read_bytes() == before and _native_keys(db_path) == []


def test_invalid_mutated_plan_fails_before_session_replacement(monkeypatch, tmp_path):
    session, _materialize, _candidate_id, _db_path = _setup(monkeypatch, tmp_path)
    research = session.load("recovery")
    path = tmp_path / "sessions" / "recovery.json"
    before = path.read_bytes()
    research.candidates[0].planned_zotero_key = "invalid"
    with pytest.raises(ValueError, match="Planned Zotero key"):
        session.save(research)
    assert path.read_bytes() == before


@pytest.mark.parametrize("owner", ["background", "family"])
def test_conflicting_plans_are_not_silently_replaced(monkeypatch, tmp_path, owner):
    session, _materialize, _candidate_id, _db_path = _setup(monkeypatch, tmp_path)
    research = session.load("recovery")
    research.candidates[0].planned_zotero_key = "PLAN1234"
    session.save(research)
    other = Candidate.from_dict(research.candidates[0].to_dict())
    other.planned_zotero_key = "PLAN5678"
    before = (tmp_path / "sessions" / "recovery.json").read_bytes()
    with pytest.raises(ValueError, match="Conflicting planned Zotero keys"):
        if owner == "background":
            research.candidates = [other]
            session.save_merge(research)
        else:
            to_version_families([research.candidates[0], other])
    assert (tmp_path / "sessions" / "recovery.json").read_bytes() == before


def test_bridge_merge_keeps_absorbed_cards_plan_after_finalization_failure(monkeypatch, tmp_path):
    session, materialize, second_id, db_path = _setup(monkeypatch, tmp_path)
    research = session.load("recovery")
    first = Candidate(title="Separate preprint", arxiv_id="2501.54321", candidate_id="first-card")
    research.candidates.insert(0, first)
    session.save(research)
    stale = session.load("recovery")
    save = session.save

    def fail_finalization(current):
        if any(candidate.materialized_zotero_key for candidate in current.candidates):
            raise OSError("synthetic absorbed-card finalization failure")
        save(current)

    with monkeypatch.context() as patch:
        patch.setattr(session, "save", fail_finalization)
        with pytest.raises(OSError, match="synthetic absorbed-card finalization failure"):
            materialize.materialize_candidate("recovery", second_id, None)
    assert _native_keys(db_path) == ["ADDED001"]
    persisted = session.load("recovery")
    assert persisted.candidates[1].planned_zotero_key == "ADDED001"
    bridge = Candidate(title="Richer bridging publication", doi=stale.candidates[1].doi,
                       arxiv_id=first.arxiv_id, abstract="The same publication across two saved cards.",
                       venue="Journal")
    stale.candidates = to_version_families([*stale.candidates, bridge])
    assert len(stale.candidates) == 1 and stale.candidates[0].candidate_id == first.candidate_id
    session.save_merge(stale)
    result = materialize.materialize_candidate("recovery", first.candidate_id, None)
    assert _native_keys(db_path) == ["ADDED001"]
    assert result["zotero_key"] == "ADDED001"


@pytest.mark.parametrize("delivery", ["planned", "materialized", "legacy_materialized"])
@pytest.mark.parametrize("transitive", [False, True])
def test_absorbed_family_recovers_only_operation_state(monkeypatch, tmp_path, delivery, transitive):
    from zotero_summarizer.services.triage import feeds

    session, materialize, second_id, db_path = _setup(monkeypatch, tmp_path)
    research = session.load("recovery")
    survivor = Candidate(title="Saved preprint", arxiv_id="2502.32109", candidate_id="survivor")
    research.candidates.append(survivor)
    retained_doi = "10.1/retained" if transitive else research.candidates[0].doi
    if transitive:
        research.candidates[0].pmid = "771"
        research.candidates.append(Candidate(
            title="Persisted richer bridge", doi="https://doi.org/10.1/retained", pmid="771",
            abstract="Persisted metadata must not replace the worker's chosen record.", venue="Journal",
        ))
    research.candidates.append(Candidate(
        title="Chosen worker record", candidate_id="dropped-no-id", planned_zotero_key="DROP1234",
    ))
    session.save(research)
    stale = session.load("recovery")
    if delivery == "planned":
        _leave_native_commit_unfinalized(monkeypatch, session, materialize, second_id)
    else:
        materialize.materialize_candidate("recovery", second_id, None)
        if delivery == "legacy_materialized":
            persisted = session.load("recovery")
            persisted.candidates[0].planned_zotero_key = ""
            session.save(persisted)
    current_only = Candidate(title="Chosen worker record", candidate_id="current-only-no-id")
    chosen = Candidate(
        title="Chosen worker record", arxiv_id=survivor.arxiv_id, doi=retained_doi,
        candidate_id=survivor.candidate_id, abstract="Current selected text", quality={"grade": "B"},
        review={"state": "reviewed"}, coverage={"full_text_available": False}, query_score=0.37,
    )
    stale.candidates = [current_only, chosen]
    selected = [cand.to_dict() for cand in stale.candidates]
    native_before = _native_state(db_path)
    session.save_merge(stale)
    restored = session.load("recovery")
    assert [cand.candidate_id for cand in restored.candidates] == ["current-only-no-id", "survivor"]
    assert restored.candidates[0].planned_zotero_key == restored.candidates[0].materialized_zotero_key == ""
    pending = restored.candidates[1]
    assert pending.planned_zotero_key == ("" if delivery == "legacy_materialized" else "ADDED001")
    assert pending.materialized_zotero_key == ("" if delivery == "planned" else "ADDED001")
    actual = [cand.to_dict() for cand in restored.candidates]
    for row in selected + actual:
        row.pop("planned_zotero_key", None)
        row.pop("materialized_zotero_key", None)
    assert actual == selected
    assert _native_state(db_path) == native_before
    monkeypatch.setattr(feeds, "_generate_zotero_key", lambda used: pytest.fail("absorbed identity was lost"))
    if delivery != "planned":
        monkeypatch.setattr(materialize, "ZoteroWriter", lambda *args: pytest.fail("delivered family rewrote native state"))
    assert materialize.materialize_candidate("recovery", "survivor", None)["zotero_key"] == "ADDED001"
    assert _native_state(db_path) == native_before and _native_keys(db_path) == ["ADDED001"]


@pytest.mark.parametrize("conflict", ["plans", "materialized", "mixed"])
def test_absorbed_transitive_conflicts_fail_without_replacement(monkeypatch, tmp_path, conflict):
    session, _materialize, _second_id, db_path = _setup(monkeypatch, tmp_path)
    research = session.load("recovery")
    research.candidates[0].pmid = "812"
    research.candidates.extend([
        Candidate(title="Survivor", arxiv_id="2503.44444", candidate_id="survivor"),
        Candidate(title="Persisted bridge", doi="10.1/retained", pmid="812"),
    ])
    session.save(research)
    stale = session.load("recovery")
    chosen = Candidate(title="Current metadata", doi="10.1/retained", arxiv_id="2503.44444",
                       candidate_id="survivor", quality={"grade": "A"})
    stale.candidates = [chosen]
    before_current = chosen.to_dict()
    persisted = session.load("recovery")
    if conflict == "plans":
        persisted.candidates[0].planned_zotero_key = "PLAN1234"
        persisted.candidates[1].planned_zotero_key = "PLAN5678"
    elif conflict == "materialized":
        persisted.candidates[0].materialized_zotero_key = "ADDED001"
        persisted.candidates[1].materialized_zotero_key = "ADDED002"
    else:
        persisted.candidates[0].materialized_zotero_key = "ADDED001"
        persisted.candidates[1].planned_zotero_key = "PLAN5678"
    session.save(persisted)
    path = tmp_path / "sessions" / "recovery.json"
    before = path.read_bytes()
    native_before = _native_state(db_path)
    with pytest.raises(ValueError, match="Conflicting.*Zotero"):
        session.save_merge(stale)
    assert path.read_bytes() == before and chosen.to_dict() == before_current
    assert _native_state(db_path) == native_before


def test_same_saved_address_without_identifiers_keeps_its_plan(monkeypatch, tmp_path):
    from zotero_summarizer.services.triage import feeds

    session, materialize, candidate_id, db_path = _setup(monkeypatch, tmp_path)
    research = session.load("recovery")
    research.candidates[0].doi = ""
    session.save(research)
    stale = session.load("recovery")
    assert stale.candidates[0].identifier_keys() == []
    _leave_native_commit_unfinalized(monkeypatch, session, materialize, candidate_id)
    stale.candidates[0].title = "Current identifierless title"
    session.save_merge(stale)
    pending = session.load("recovery").candidates[0]
    assert pending.candidate_id == candidate_id and pending.identifier_keys() == []
    assert pending.title == "Current identifierless title" and pending.planned_zotero_key == "ADDED001"
    assert pending.materialized_zotero_key == "" and pending.in_library is None
    monkeypatch.setattr(feeds, "_generate_zotero_key", lambda used: pytest.fail("address-only identity lost"))
    assert materialize.materialize_candidate("recovery", candidate_id, None)["zotero_key"] == "ADDED001"
    assert _native_keys(db_path) == ["ADDED001"]


@pytest.mark.parametrize("drop_all", [False, True])
def test_omitted_unrelated_families_are_not_resurrected_or_reconciled(monkeypatch, tmp_path, drop_all):
    session, _materialize, _candidate_id, db_path = _setup(monkeypatch, tmp_path)
    research = session.load("recovery")
    research.candidates[0].planned_zotero_key = "KEEP1234"
    title = research.candidates[0].title
    research.candidates.extend([
        Candidate(title=title, doi="10.1/unrelated", candidate_id="omitted-one", planned_zotero_key="DROP1234"),
        Candidate(title=title, doi="10.1/unrelated", candidate_id="omitted-two", planned_zotero_key="DROP5678"),
    ])
    session.save(research)
    current = session.load("recovery")
    current.candidates = [] if drop_all else current.candidates[:1]
    selected = current.to_dict()
    native_before = _native_state(db_path)
    session.save_merge(current)
    assert session.load("recovery").to_dict() == selected
    assert _native_state(db_path) == native_before and _native_keys(db_path) == []
