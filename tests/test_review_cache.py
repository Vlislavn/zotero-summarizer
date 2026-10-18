"""Deep-review cache primitives (split out of deep_review for the LOC cap).

Pins ``copy_review`` — the persistence step that carries an IN-PLACE Today review
(cached under ``stable_feed_key``) onto the new library ``item_key`` when the paper
is materialized into Zotero, so the review shows in the library too.
"""
from __future__ import annotations

import pytest

from zotero_summarizer.services.library import _review_cache


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: tmp_path / "deep_reviews.json")
    yield


def test_copy_review_persists_under_new_key():
    _review_cache._write_one("feed:d:abc", {"quality": {"grade": "A"}})
    assert _review_cache.copy_review("feed:d:abc", "ZNEW") is True
    assert _review_cache.get_cached_review("ZNEW") == {"quality": {"grade": "A"}}
    # The source entry is untouched (copy, not move).
    assert _review_cache.get_cached_review("feed:d:abc") == {"quality": {"grade": "A"}}


def test_copy_review_is_noop_on_absent_or_same_key():
    assert _review_cache.copy_review("missing", "Z") is False       # nothing cached
    _review_cache._write_one("K1", {"quality": {}})
    assert _review_cache.copy_review("K1", "K1") is False           # same key
    assert _review_cache.copy_review("", "Z") is False              # empty src
    assert _review_cache.copy_review("K1", "") is False             # empty dst


def test_cached_review_keys_reports_all():
    _review_cache._write_one("A", {"x": 1})
    _review_cache._write_one("B", {"x": 2})
    assert _review_cache.cached_review_keys() == {"A", "B"}


def test_corrupt_cache_is_quarantined_without_overwriting_prior_recovery(tmp_path):
    path = _review_cache._cache_path()
    corrupt = b'{"reviews":'
    path.write_bytes(corrupt)

    assert _review_cache.cached_review_keys() == set()
    assert not path.exists()
    assert (tmp_path / "deep_reviews.json.corrupt").read_bytes() == corrupt

    path.write_text("not json", encoding="utf-8")
    assert _review_cache.current_reviews() == {}
    assert (tmp_path / "deep_reviews.json.corrupt-1").read_text(encoding="utf-8") == "not json"


def test_current_review_requires_matching_source_and_generation_identity(monkeypatch):
    from zotero_summarizer.services.library import _review_identity

    identity = {
        "generation_sha256": "a" * 64, "source_sha256": "b" * 64,
        "source_kind": "library", "source_path": "/paper.pdf", "focus_prompt": "",
    }
    current = {
        "review_contract_version": _review_cache.REVIEW_CONTRACT_VERSION,
        "review_identity": identity, "digest": {},
    }
    _review_cache._write_one("CURRENT", current)
    _review_cache._write_one("LEGACY", {"digest": {}})
    monkeypatch.setattr(_review_identity, "current_review_identity", lambda key, stored: identity)

    assert _review_cache.get_current_review("CURRENT") == current
    assert _review_cache.get_current_review("LEGACY") is None
    assert _review_cache.current_review_keys() == {"CURRENT"}

    monkeypatch.setattr(
        _review_identity, "current_review_identity",
        lambda key, stored: {**identity, "source_sha256": "c" * 64},
    )
    assert _review_cache.get_current_review("CURRENT") is None
    assert _review_cache.current_review_keys() == set()


def _accepted_audit_entry():
    from test_digest_verification import PAPER, _Verifier, _digest
    from zotero_summarizer.services.library.quality_review import assess_digest
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config
    from zotero_summarizer.services.library._digest_audit import bind_cached_audit

    class Generator:
        def pydantic_prompt(self, **kwargs):
            return _digest()

    audit = {}
    digest = assess_digest(title="Paper", full_text=PAPER, config=_default_goals_config(),
                           llm=Generator(), verifier_llm=_Verifier(), verification_audit=audit)
    entry = {"digest": digest.model_dump(mode="json"), "review_identity": {
        "source_sha256": "", "source_path": "", "source_kind": "synthetic_extraction",
    }, "verification_audit": audit}
    return bind_cached_audit(entry)


def test_local_audit_export_retains_exact_cached_evidence_without_source_writes(tmp_path):
    entry = _accepted_audit_entry()
    _review_cache._write_one("ITEM", entry)
    path = _review_cache._cache_path()
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir() if p.is_file()}

    exported = _review_cache.export_review_audit("ITEM")

    assert exported["audit_status"] == "available"
    assert exported["verification_audit"] == entry["verification_audit"]
    assert exported["digest"] == entry["digest"]
    assert _review_cache._read_all()["ITEM"] == entry
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir() if p.is_file()}
    assert before == after and path.exists()


@pytest.mark.parametrize("component", ["digest", "source", "selected", "extraction", "verifier"])
def test_audit_invalidation_is_independent_for_each_bound_identity(component):
    entry = _accepted_audit_entry()
    audit = entry["verification_audit"]
    if component == "digest":
        entry["digest"]["read_why"] = "Post-verification policy edit."
    elif component == "source":
        entry["review_identity"]["source_sha256"] = "a" * 64
    elif component == "selected":
        audit["source"]["selected_text_sha256"] = "a" * 64
    else:
        audit["identity"][component + "_contract_sha256"] = "a" * 64
    _review_cache._write_one("ITEM", entry)

    exported = _review_cache.export_review_audit("ITEM")

    assert exported["audit_status"] == "stale"
    assert exported["verification_audit"] is None


def test_legacy_audit_export_reports_unavailable_without_inventing_evidence():
    _review_cache._write_one("LEGACY", {"digest": {"tldr": "Legacy text."}})

    exported = _review_cache.export_review_audit("LEGACY")

    assert exported["audit_status"] == "unavailable"
    assert exported["reason"] == "legacy_audit_unavailable"
    assert exported["verification_audit"] is None


@pytest.mark.parametrize("corrupt", [b'{"reviews":', b'{"reviews": []}', b'\xff'])
def test_readonly_audit_export_fails_on_corruption_without_quarantine(tmp_path, corrupt):
    path = _review_cache._cache_path()
    path.write_bytes(corrupt)
    before = path.stat().st_mtime_ns

    with pytest.raises(ValueError, match="deep-review cache"):
        _review_cache.export_review_audit("ITEM")

    assert path.read_bytes() == corrupt and path.stat().st_mtime_ns == before
    assert list(tmp_path.iterdir()) == [path]


def test_audit_rechecks_same_size_same_mtime_source_bytes_without_changing_saved_entry(tmp_path):
    import hashlib
    import os
    from zotero_summarizer.services.library import _digest_audit

    source = tmp_path / "owned-synthetic.pdf"
    original, replacement = b"synthetic source alpha", b"synthetic source bravo"
    assert len(original) == len(replacement)
    source.write_bytes(original)
    original_stat = source.stat()
    entry = _accepted_audit_entry()
    entry["review_identity"].update(source_path=str(source), source_sha256=hashlib.sha256(original).hexdigest())
    entry = _digest_audit.bind_cached_audit(entry)
    _review_cache._write_one("ITEM", entry)
    cache, mtime = _review_cache._cache_path().read_bytes(), _review_cache._cache_path().stat().st_mtime_ns
    assert _review_cache.export_review_audit("ITEM")["audit_status"] == "available"

    source.write_bytes(replacement)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert source.stat().st_size == original_stat.st_size
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
    exported = _review_cache.export_review_audit("ITEM")
    assert exported["audit_status"] == "stale" and exported["reason"] == "source_bytes_changed"
    assert exported["verification_audit"] is None
    assert _review_cache._cache_path().read_bytes() == cache
    assert _review_cache._cache_path().stat().st_mtime_ns == mtime
    assert _review_cache._read_all(readonly=True)["ITEM"] == entry


@pytest.mark.parametrize("component", ["extraction", "verifier"])
def test_audit_rechecks_current_contracts_with_saved_payload_and_generation_identity_unchanged(component, monkeypatch):
    from zotero_summarizer.services.library import _digest_audit

    entry = _accepted_audit_entry()
    entry["review_identity"]["generation_sha256"] = "g" * 64
    entry = _digest_audit.bind_cached_audit(entry)
    _review_cache._write_one("ITEM", entry)
    saved = _review_cache._cache_path().read_bytes()
    current = _digest_audit._contract_hashes()
    assert _review_cache.export_review_audit("ITEM")["audit_status"] == "available"
    changed = {**current, component + "_contract_sha256": "c" * 64}
    monkeypatch.setattr(_digest_audit, "_contract_hashes", lambda: changed)

    exported = _review_cache.export_review_audit("ITEM")
    assert exported["audit_status"] == "stale" and exported["reason"] == "audit_contract_changed"
    assert exported["verification_audit"] is None
    assert _review_cache._cache_path().read_bytes() == saved
    assert _review_cache._read_all(readonly=True)["ITEM"] == entry


def test_real_review_policy_cache_export_separates_verified_and_final_digest_identity(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from test_digest_verification import PAPER, _Verifier, _digest
    from test_review_source_boundary import _Generator
    from zotero_summarizer.services.library import deep_review, _digest_audit
    from zotero_summarizer.services.library.review_fleet import propose
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    source = tmp_path / "owned-policy-source.pdf"
    source.write_bytes(b"synthetic owned PDF representation")
    reader = SimpleNamespace(get_item_detail=lambda key: {"title": "Paper", "pdf_path": str(source)})
    extractor = SimpleNamespace(extract_text=lambda path: PAPER)
    verified = _digest().model_copy(update={"key_findings": ["The evaluation uses held-out data."]})
    generator, verifier = _Generator(verified), _Verifier()
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers", lambda ctx: (None, None, None, None, None))
    entry = deep_review._review_one({"item_key": "feed:owned"}, reader=reader,
                                   config=_default_goals_config(), extractor=extractor, llm=generator,
                                   llm_map=verifier, quality_enabled=True)
    assert entry["model_read_decision"] == "skip" and entry["digest"]["read_decision"] == ""
    assert entry["digest"]["read_why"] == "Goals not assessed; reading decision withheld."
    assert entry["review_identity"]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    _review_cache._write_one("feed:owned", entry)
    saved, mtime = _review_cache._cache_path().read_bytes(), _review_cache._cache_path().stat().st_mtime_ns
    monkeypatch.setattr(propose, "apply_reading_policy", lambda *args: pytest.fail("Exporter must not project policy"))

    exported = deep_review.export_review_audit("feed:owned")
    audit = exported["verification_audit"]
    assert exported["audit_status"] == "available" and exported["digest"] == entry["digest"]
    assert audit == entry["verification_audit"]
    assert audit["identity"]["verified_digest_sha256"] == _digest_audit.canonical_hash(verified.model_dump(mode="json"))
    assert audit["identity"]["cached_digest_sha256"] == _digest_audit.canonical_hash(entry["digest"])
    assert audit["identity"]["cached_digest_sha256"] != audit["identity"]["verified_digest_sha256"]
    assert audit["post_verification_changes"] == ["read_decision", "read_why"]
    assert audit["verified_digest"]["read_why"] == verified.read_why
    assert all(claim["matches_cached_field"] for claim in audit["claims"])
    assert audit["lineage"][0]["digest_sha256"] == audit["identity"]["verified_digest_sha256"]
    assert len(generator.prompts) == 1 and verifier.calls == 1
    assert _review_cache._cache_path().read_bytes() == saved
    assert _review_cache._cache_path().stat().st_mtime_ns == mtime


def test_missing_pdf_export_distinguishes_source_unavailable_from_legacy_audit():
    _review_cache._write_one("NO_PDF", {"digest": None, "needs_pdf": True})
    exported = _review_cache.export_review_audit("NO_PDF")
    assert exported["audit_status"] == "unavailable" and exported["reason"] == "source_unavailable"
    assert exported["verification_audit"] is None


@pytest.mark.parametrize("same_metadata", [False, True], ids=["new_metadata", "same_size_same_mtime"])
def test_review_worker_rejects_source_replacement_after_extraction_before_binding(tmp_path, monkeypatch, same_metadata):
    import hashlib
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from test_digest_verification import PAPER, _Verifier, _digest
    from test_review_source_boundary import _Generator
    from zotero_summarizer.services.library import deep_review, _review_identity
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    source = tmp_path / "owned-changing-source.pdf"
    original, replacement = PAPER.encode(), b"R" * len(PAPER.encode())
    source.write_bytes(original)
    original_stat = source.stat()
    assert _review_identity._source_sha(str(source)) == hashlib.sha256(original).hexdigest()
    extracted, rebuilds = [], []

    def extract(path):
        text = Path(path).read_text()
        extracted.append(text)
        return text

    class ReplacingGenerator(_Generator):
        def pydantic_prompt(self, **kwargs):
            source.write_bytes(replacement)
            mtime = original_stat.st_mtime_ns if same_metadata else original_stat.st_mtime_ns + 1_000_000
            os.utime(source, ns=(original_stat.st_atime_ns, mtime))
            assert source.stat().st_size == original_stat.st_size
            assert source.stat().st_mtime_ns == mtime
            return super().pydantic_prompt(**kwargs)

    key = "feed:owned-source-race:" + str(same_metadata)
    _review_cache._write_one(key, {"digest": {"tldr": "Prior cached entry."}})
    cache = _review_cache._cache_path().read_bytes()
    generator, verifier = ReplacingGenerator(_digest()), _Verifier()
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers", lambda ctx: (None, None, None, None, None))
    monkeypatch.setattr(deep_review, "_try_rebuild_render", lambda item_key: rebuilds.append(item_key))
    ctx = {"reader": SimpleNamespace(get_item_detail=lambda item_key: {"title": "Paper", "pdf_path": str(source)}),
           "config": _default_goals_config(), "extractor": SimpleNamespace(extract_text=extract),
           "llm": generator, "llm_map": verifier, "quality_enabled": True}

    deep_review._review_worker({"item_key": key}, ctx, "")

    result = deep_review.status(key)
    assert result["status"] == "error", "Changed PDF must not publish an accepted audit bound to replacement bytes"
    assert "source changed" in result["error"].lower()
    assert extracted == [PAPER] and source.read_bytes() == replacement
    assert len(generator.prompts) == 1 and verifier.calls == 1
    assert _review_cache._cache_path().read_bytes() == cache and rebuilds == []


def _owned_audit_contract_tree(tmp_path, monkeypatch):
    from pathlib import Path
    from zotero_summarizer.services.library import _digest_audit

    installed = Path(_digest_audit.__file__).parents[2]
    owned = tmp_path / "owned-contract-source"
    paths = ["integrations/pdf.py", "services/library/_review_text.py", "services/faithbench/_corpus.py",
             "services/library/_digest_verification.py", "services/library/_grounding.py",
             "services/library/_prompt_security.py", "services/faithbench/_build_claims.py",
             "models/triage.py", "services/library/_digest_audit.py",
             "services/faithbench/_constants.py", "storage/corpus_bm25.py"]
    for relative in paths:
        destination = owned / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((installed / relative).read_bytes())
    monkeypatch.setattr(_digest_audit, "__file__", str(owned / "services/library/_digest_audit.py"))
    return owned


@pytest.mark.parametrize("dependency,before,after,affected", [
    ("services/faithbench/_constants.py", b"CHUNK_CHARS = 1_200", b"CHUNK_CHARS = 1_300", {"extraction", "verifier"}),
    ("storage/corpus_bm25.py", b"[a-z0-9]+", b"[a-z]+", {"extraction"}),
], ids=["chunk_defaults", "selection_tokenizer"])
def test_audit_rechecks_owned_dependency_bytes_without_changing_saved_entry(tmp_path, monkeypatch, dependency, before, after, affected):
    from zotero_summarizer.services.library import _digest_audit

    owned = _owned_audit_contract_tree(tmp_path, monkeypatch)
    entry = _accepted_audit_entry()
    _review_cache._write_one("ITEM", entry)
    saved, mtime = _review_cache._cache_path().read_bytes(), _review_cache._cache_path().stat().st_mtime_ns
    original_contracts = _digest_audit._contract_hashes()
    assert _review_cache.export_review_audit("ITEM")["audit_status"] == "available"
    target = owned / dependency
    original = target.read_bytes()
    assert original.count(before) == 1
    target.write_bytes(original.replace(before, after))

    exported = _review_cache.export_review_audit("ITEM")
    assert exported["audit_status"] == "stale", "Consequential dependency bytes must invalidate an unchanged saved audit"
    assert exported["reason"] == "audit_contract_changed" and exported["verification_audit"] is None
    current_contracts = _digest_audit._contract_hashes()
    assert {kind for kind in ("extraction", "verifier") if original_contracts[kind + "_contract_sha256"] !=
            current_contracts[kind + "_contract_sha256"]} == affected
    assert _review_cache._cache_path().read_bytes() == saved
    assert _review_cache._cache_path().stat().st_mtime_ns == mtime
    assert _review_cache._read_all(readonly=True)["ITEM"] == entry


def test_library_source_drift_preserves_prior_note_and_cache_before_publication(tmp_path, monkeypatch):
    import json
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from test_digest_verification import PAPER, _Verifier, _digest
    from test_review_source_boundary import _Generator
    from zotero_summarizer.services.library import deep_review, review_detail
    from zotero_summarizer.services.zotero import zotero
    from zotero_summarizer.services.setup.bootstrap import _default_goals_config

    key = "LIBR0038"
    assert review_detail.classify_item_key(key) == review_detail.SOURCE_LIBRARY
    source = tmp_path / "owned-library-source.pdf"
    source.write_text(PAPER)
    original_stat = source.stat()
    replacement = b"R" * len(source.read_bytes())
    prior_note = tmp_path / "owned-prior-digest-note.json"
    prior_note.write_text(json.dumps({"tldr": "Prior owned library digest note."}))
    note_before = prior_note.read_bytes()
    _review_cache._write_one(key, {"digest": {"tldr": "Prior cached library digest."}})
    cache_before = _review_cache._cache_path().read_bytes()
    extracted, writes, rebuilds = [], [], []

    def extract(path):
        text = Path(path).read_text()
        extracted.append(text)
        return text

    class ReplacingGenerator(_Generator):
        def pydantic_prompt(self, **kwargs):
            source.write_bytes(replacement)
            os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            return super().pydantic_prompt(**kwargs)

    def scripted_note_writer(item_key, digest):
        writes.append(item_key)
        prior_note.write_text(json.dumps(digest.model_dump(mode="json")))

    generator, verifier = ReplacingGenerator(_digest()), _Verifier()
    monkeypatch.setattr(zotero, "zotero_upsert_digest_note", scripted_note_writer)
    monkeypatch.setattr(deep_review._deep_review_layers, "extra_layers", lambda ctx: (None, None, None, None, None))
    monkeypatch.setattr(deep_review, "_try_rebuild_render", lambda item_key: rebuilds.append(item_key))
    ctx = {"reader": SimpleNamespace(get_item_detail=lambda item_key: {"title": "Paper", "pdf_path": str(source)}),
           "config": _default_goals_config(), "extractor": SimpleNamespace(extract_text=extract),
           "llm": generator, "llm_map": verifier, "quality_enabled": True}

    deep_review._review_worker({"item_key": key}, ctx, "")

    result = deep_review.status(key)
    assert result["status"] == "error" and "source changed" in result["error"].lower()
    assert extracted == [PAPER] and source.read_bytes() == replacement
    assert source.stat().st_size == original_stat.st_size
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert len(generator.prompts) == 1 and verifier.calls == 1
    assert writes == [], "Known source drift must reject before the first digest-note upsert"
    assert prior_note.read_bytes() == note_before
    assert _review_cache._cache_path().read_bytes() == cache_before and rebuilds == []


@pytest.mark.parametrize("boundary", ["review_identity", "audit_export"])
def test_both_source_hash_paths_work_without_file_digest(tmp_path, monkeypatch, boundary):
    import hashlib
    import os
    from pathlib import Path
    from zotero_summarizer.services.library import _digest_audit, _review_identity

    source = tmp_path / "owned-portability-source.pdf"
    original = b"Owned source bytes across multiple hash chunks.\n" * 4_000
    replacement = b"R" * len(original)
    source.write_bytes(original)
    opening = source.stat()
    expected = hashlib.sha256(original).hexdigest()
    entry = _accepted_audit_entry()
    entry["review_identity"].update(source_path=str(source), source_sha256=expected)
    entry = _digest_audit.bind_cached_audit(entry)
    _review_cache._write_one("OWNED", entry)
    cache = _review_cache._cache_path()
    saved = cache.read_bytes(), cache.stat().st_mtime_ns

    def observe():
        if boundary == "review_identity":
            return _review_identity._source_sha(str(source))
        return _review_cache.export_review_audit("OWNED")

    monkeypatch.delattr(hashlib, "file_digest", raising=False)
    assert _review_identity._source_sha("") == ""
    assert _review_identity._source_sha(str(tmp_path / "absent.pdf")) == "missing"

    result = observe()

    if boundary == "review_identity":
        assert result == expected
    else:
        assert result["audit_status"] == "available" and result["verification_audit"] == entry["verification_audit"]
    source.write_bytes(replacement)
    os.utime(source, ns=(opening.st_atime_ns, opening.st_mtime_ns))
    assert source.stat().st_size == opening.st_size and source.stat().st_mtime_ns == opening.st_mtime_ns
    changed = observe()
    if boundary == "review_identity":
        assert changed == hashlib.sha256(replacement).hexdigest() and changed != expected
    else:
        assert changed["audit_status"] == "stale" and changed["reason"] == "source_bytes_changed"
        assert changed["verification_audit"] is None
    original_open = Path.open

    def denied_source_open(path, *args, **kwargs):
        if path == source:
            raise PermissionError("Owned source I/O denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied_source_open)
    with pytest.raises(PermissionError, match="Owned source I/O denied"):
        observe()
    source.unlink()
    missing = observe()
    if boundary == "review_identity":
        assert missing == "missing"
    else:
        assert missing["audit_status"] == "unavailable" and missing["reason"] == "source_unavailable"
    assert (cache.read_bytes(), cache.stat().st_mtime_ns) == saved
