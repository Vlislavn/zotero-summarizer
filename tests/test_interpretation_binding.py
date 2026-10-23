"""Real native/audit/cache machine contracts; no semantic or human-warrant oracle."""
import builtins
from copy import deepcopy
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_digest_verification import PAPER, _Verifier, _claims, _digest
from tests.test_interpretation_producer import _base, _card
from tests.test_interpretation_verification import _table
from tests.test_review_cache import _accepted_audit_entry, _isolate_cache
from zotero_summarizer.services.library import _digest_audit, _review_cache, _review_identity, quality_review
from zotero_summarizer.services.library.review_eligibility import usable_review
from zotero_summarizer.services.library.review_fleet import propose
from zotero_summarizer.services.setup.bootstrap import _default_goals_config

C = "interpretation-digest-response-v1"
TEXT = PAPER + " " + "Additional extracted study context uses held-out comparisons. " * 24


@pytest.fixture
def config(monkeypatch):
    value = _default_goals_config()
    monkeypatch.setattr(_review_identity, "settings", lambda: SimpleNamespace(summary_timeout_seconds=120))
    monkeypatch.setattr(_review_identity, "state", lambda: SimpleNamespace(app_state=SimpleNamespace(config=value)))
    return value


def _native_entry(tmp_path, config, *, card=None, missing=False, optional_passage=False):
    base = _base().model_copy(update={"key_findings": ["The held-out finding is described. Transfer remains unresolved."]})
    card = None if missing else deepcopy(card if card is not None else _card())
    generator = Mock()
    generator.pydantic_prompt.return_value = {**base.model_dump(exclude_unset=True), "interpretation": card}
    verifier = Mock() if optional_passage else _Verifier()
    if optional_passage:
        count = len(_claims(base))
        verifier.pydantic_prompt.return_value = {"checks": list(reversed(_table(count + 5, optional_start=count)))}
    source = tmp_path / "owned-source.pdf"
    source.write_text(TEXT, encoding="utf-8")
    identity = _review_identity.build_review_identity(config=config, pdf_path=str(source), source_kind="override",
                                                     focus_prompt="", interpretation_schema=C)
    out, audit = {}, {}
    digest = quality_review.assess_digest(title="Paper", full_text=TEXT, config=config, llm=generator,
                                         verifier_llm=verifier, interpretation_out=out, verification_audit=audit)
    entry = {"review_contract_version": 3, "review_identity": identity,
             "digest": digest.model_dump(mode="json"), "interpretation": out, "verification_audit": audit}
    return _digest_audit.bind_cached_audit(entry, interpretation_schema=C)


def _assert_base_only(view):
    assert usable_review(view)
    assert view["interpretation_qualification"]["status"] == "unavailable"
    assert view["interpretation_qualification"]["audit_status"] == "available"
    assert "interpretation" not in view and "verification_audit" not in view
    assert "verified_interpretation" not in view


def test_verification_captures_optional_native_evidence_without_changing_base_oracles():
    generator, verifier = Mock(), Mock()
    generator.pydantic_prompt.return_value = {**_base().model_dump(exclude_unset=True), "interpretation": _card()}
    count = len(_claims(_base()))
    verifier.pydantic_prompt.return_value = {"checks": list(reversed(_table(count + 5, optional_start=count)))}
    out, audit = {}, {}
    digest = quality_review.assess_digest(title="Paper", full_text=TEXT, config=_default_goals_config(),
                                         llm=generator, verifier_llm=verifier, interpretation_out=out,
                                         verification_audit=audit)
    assert [passage["id"] for passage in audit["passages"]] == [0]
    assert {claim["field"] for claim in audit["claims"]} == {"tldr", "key_findings"}
    evidence = audit["interpretation_evidence"]
    assert evidence["status"] == "accepted_native_support"
    assert [passage["id"] for passage in evidence["passages"]] == [1]
    assert all(claim["evidence"] == [1] for claim in evidence["claims"])
    assert [claim["index"] for claim in evidence["claims"]] == list(range(count, count + 5))
    assert {claim["field"] for claim in evidence["basis_claims"]} == {"methods", "limitations", "key_weakness", "parameters"}
    assert audit["verified_interpretation"] == out
    assert audit["identity"]["verified_interpretation_sha256"] == _digest_audit.canonical_hash(out)
    assert "interpretation_contract_sha256" in audit["identity"]
    assert out["scientific_verification"] == "unverified" and out["origin"] == "ai_generated"
    assert "interpretation" not in digest.model_dump()
    for passage in evidence["passages"]:
        span = passage["selected_span"]
        assert passage["text"] == TEXT[span["start"]:span["end"]]
        assert passage["original_fragments"] == [{"selected_span": span, "original_span": span, "mapping": "exact"}]
        assert passage["pdf_page"] is None
    assert generator.pydantic_prompt.call_count == verifier.pydantic_prompt.call_count == 1


def test_cache_write_to_current_c_read_qualifies_whole_finding_and_basis(tmp_path, config):
    entry = _native_entry(tmp_path, config, optional_passage=True)
    _review_cache._write_one("ITEM", entry)
    path = _review_cache._cache_path()
    before = path.read_bytes(), path.stat().st_mtime_ns
    view = _review_cache.get_current_review("ITEM", interpretation_schema=C)
    assert usable_review(view) and view["interpretation_qualification"]["status"] == "available"
    assert view["interpretation"] == entry["interpretation"]
    assert view["interpretation"]["scientific_verification"] == "unverified"
    binding = entry["verification_audit"]["interpretation_bindings"][0]
    whole = entry["verification_audit"]["verified_digest"]["key_findings"][0]
    assert binding["finding_sha256"] == _digest_audit.canonical_hash(whole)
    assert all(binding["finding_sha256"] != claim["claim_sha256"] for claim in entry["verification_audit"]["claims"])
    assert binding["basis_sha256"]["/parameters/dataset"] == _digest_audit.canonical_hash("ImageNet")
    assert binding["matches_cached_finding"] is True and all(binding["matches_cached_basis"].values())
    assert view["verification_audit"]["contradiction_adjudication"] == "not_performed"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_one_readonly_raw_snapshot_survives_concurrent_file_replacement(tmp_path, config, monkeypatch):
    entry = _native_entry(tmp_path, config)
    _review_cache._write_one("ITEM", entry)
    path = _review_cache._cache_path()
    replacement = deepcopy(entry)
    replacement["digest"]["tldr"] = "Concurrent replacement."
    captured, replaced = Event(), Event()
    original_read = _review_cache._read_all
    reads, writer_reads = [], []

    def replace():
        assert captured.wait(5)
        _review_cache._write_all({"ITEM": replacement})
        replaced.set()

    def one_read(*, readonly=False):
        if not readonly:
            writer_reads.append(readonly)
            return original_read(readonly=readonly)
        reads.append(readonly)
        raw = original_read(readonly=readonly)
        captured.set()
        assert replaced.wait(5)
        return raw

    thread = Thread(target=replace)
    thread.start()
    monkeypatch.setattr(_review_cache, "_read_all", one_read)
    policy = Mock(wraps=propose.apply_reading_policy)
    monkeypatch.setattr(propose, "apply_reading_policy", policy)
    try:
        view = _review_cache.get_current_review("ITEM", interpretation_schema=C)
    finally:
        thread.join(5)
    assert not thread.is_alive() and reads == [True] and policy.call_count == 1
    assert writer_reads == [False]
    assert view["digest"]["tldr"] == entry["digest"]["tldr"]
    assert view["interpretation_qualification"]["status"] == "available"
    assert original_read(readonly=True)["ITEM"] == replacement and path.exists()


def test_policy_runs_after_raw_audit_and_cannot_falsely_stale_it(tmp_path, config, monkeypatch):
    entry = _native_entry(tmp_path, config)
    _review_cache._write_one("ITEM", entry)
    assert entry["digest"]["read_decision"] == "skip"
    observed = []
    status = _digest_audit.cached_audit_status
    policy = propose.apply_reading_policy

    def audit_first(raw, **kwargs):
        observed.append("audit")
        assert raw["digest"] == entry["digest"]
        return status(raw, **kwargs)

    def policy_second(*args):
        observed.append("policy")
        return policy(*args)

    monkeypatch.setattr(_digest_audit, "cached_audit_status", audit_first)
    monkeypatch.setattr(propose, "apply_reading_policy", policy_second)
    view = _review_cache.get_current_review("ITEM", interpretation_schema=C)
    assert observed == ["audit", "policy"]
    assert view["digest"]["read_decision"] == "" and view["model_read_decision"] == "skip"
    assert view["interpretation_qualification"]["status"] == "available"
    assert _review_cache._read_all(readonly=True)["ITEM"] == entry


@pytest.mark.parametrize("mutation", ["character", "whitespace", "number", "reorder", "basis", "false_basis", "index"])
def test_exact_current_finding_basis_and_index_changes_withhold_card_not_valid_base(tmp_path, config, monkeypatch, mutation):
    entry = _native_entry(tmp_path, config)
    _review_cache._write_one("ITEM", entry)
    policy = propose.apply_reading_policy

    def changed(*args):
        digest, raw, flags = policy(*args)
        digest = deepcopy(digest)
        if mutation == "basis":
            digest["methods"] += " "
        elif mutation == "false_basis":
            digest["parameters"]["dataset"] = "ImageNetX"
        elif mutation == "index":
            digest["key_findings"] = ["Other finding.", *digest["key_findings"]]
        elif mutation == "reorder":
            digest["key_findings"] = [digest["tldr"], *digest["key_findings"]]
        else:
            digest["key_findings"][0] += {"character": "!", "whitespace": " ", "number": " 1"}[mutation]
        return digest, raw, flags

    monkeypatch.setattr(propose, "apply_reading_policy", changed)
    before = _review_cache._cache_path().read_bytes()
    view = _review_cache.get_current_review("ITEM", interpretation_schema=C)
    _assert_base_only(view)
    assert view["interpretation_qualification"]["reason"] == "interpretation_reference_changed"
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize("mutation", ["rationale", "native_check", "origin", "missing"])
def test_payload_only_tamper_cannot_leak_through_nested_audit_and_preserves_base(tmp_path, config, mutation):
    entry = _native_entry(tmp_path, config)
    if mutation == "rationale":
        entry["interpretation"]["items"][0]["rationale"] += " "
    elif mutation == "native_check":
        entry["interpretation"]["native_support"]["validated_checks"][0]["evidence"] = [99]
    elif mutation == "origin":
        entry["interpretation"]["origin"] = "author_approved"
    else:
        del entry["interpretation"]
    _review_cache._write_one("ITEM", entry)
    before = _review_cache._cache_path().read_bytes()
    assert _digest_audit.cached_audit_status(entry, interpretation_schema=C) == ("available", "")
    view = _review_cache.get_current_review("ITEM", interpretation_schema=C)
    _assert_base_only(view)
    assert _review_cache._cache_path().read_bytes() == before
    assert _review_cache._read_all(readonly=True)["ITEM"] == entry


@pytest.mark.parametrize("card", [None, {"items": []}, _card("Unreported 99999%."), _card()])
def test_missing_malformed_rejected_or_unverified_optional_preserves_only_valid_base(tmp_path, config, card):
    entry = _native_entry(tmp_path, config, card=card, missing=card is None)
    if card == _card():
        audit = entry["verification_audit"]
        for key in ("verified_interpretation", "interpretation_bindings", "interpretation_evidence"):
            audit.pop(key, None)
        for key in ("verified_interpretation_sha256", "interpretation_contract_sha256"):
            audit["identity"].pop(key, None)
        entry = _digest_audit.bind_cached_audit(entry, interpretation_schema=C)
    _review_cache._write_one("ITEM", entry)
    view = _review_cache.get_current_review("ITEM", interpretation_schema=C)
    _assert_base_only(view)
    assert view["interpretation_qualification"]["reason"] == "interpretation_not_verified"


@pytest.mark.parametrize("component", ["base", "audit", "binding", "optional_passage", "source"])
def test_base_artifact_audit_binding_or_source_tamper_is_not_card_only_fallback(tmp_path, config, component):
    entry = _native_entry(tmp_path, config, optional_passage=True)
    audit = entry["verification_audit"]
    if component == "base":
        entry["digest"]["tldr"] += "!"
    elif component == "audit":
        audit["source"]["selected_text_sha256"] = "a" * 64
    elif component == "binding":
        audit["interpretation_bindings"][0]["finding_sha256"] = "a" * 64
    elif component == "optional_passage":
        audit["interpretation_evidence"]["passages"][0]["text"] += "!"
    else:
        source = Path(entry["review_identity"]["source_path"])
        source.write_bytes(b"R" * source.stat().st_size)
    _review_cache._write_one("ITEM", entry)
    before = _review_cache._cache_path().read_bytes()
    assert _review_cache.get_current_review("ITEM", interpretation_schema=C) is None
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize("defect", ["missing_claim", "invalid_evidence", "invalid_digest", "bad_contract"])
def test_malformed_base_audit_raises_instead_of_optional_failure(tmp_path, config, defect):
    entry = _native_entry(tmp_path, config)
    audit = entry["verification_audit"]
    if defect == "missing_claim":
        audit["claims"] = audit["claims"][1:]
    elif defect == "invalid_evidence":
        audit["claims"][0]["evidence"] = [999]
    elif defect == "invalid_digest":
        entry["digest"]["soundness"] = 0
    else:
        audit["contract"] = "unknown"
    _review_cache._write_one("ITEM", entry)
    with pytest.raises(ValueError):
        _review_cache.get_current_review("ITEM", interpretation_schema=C)


@pytest.mark.parametrize("corrupt", [b'{"reviews":', b'{"reviews": []}', b'\xff'])
def test_c_current_read_is_readonly_on_corrupt_cache(tmp_path, corrupt):
    path = _review_cache._cache_path()
    path.write_bytes(corrupt)
    before = path.stat().st_mtime_ns
    with pytest.raises(ValueError, match="deep-review cache"):
        _review_cache.get_current_review("ITEM", interpretation_schema=C)
    assert path.read_bytes() == corrupt and path.stat().st_mtime_ns == before
    assert list(tmp_path.iterdir()) == [path]


def test_off_stored_c_is_not_a_schema_import_or_optional_output_request(monkeypatch):
    entry = _accepted_audit_entry()
    entry["review_contract_version"] = 3
    entry["review_identity"]["interpretation_schema"] = C
    entry["interpretation"] = {"items": ["must never be exposed"]}
    entry["verification_audit"]["interpretation_evidence"] = {"status": "accepted_native_support"}
    entry["verification_audit"]["verified_interpretation"] = entry["interpretation"]
    _review_cache._write_one("ITEM", entry)
    no_c = Mock(side_effect=AssertionError("unexpected C qualification/schema/contract IO"))
    original_import = builtins.__import__

    def no_c_import(name, *args, **kwargs):
        assert name != "zotero_summarizer.models.interpretation"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_c_import)
    monkeypatch.setattr(_digest_audit, "_contract_hashes", no_c)
    monkeypatch.setattr(_digest_audit, "qualify_interpretation", no_c, raising=False)
    monkeypatch.setattr(_review_identity, "_generation_sources", no_c)
    monkeypatch.setattr(_review_identity, "build_review_identity", no_c)
    assert _review_cache.get_cached_review("ITEM") is None
    assert _review_cache.get_current_review("ITEM") is None
    exported = _review_cache.export_review_audit("ITEM")
    assert exported == {"item_key": "ITEM", "digest": entry["digest"], "audit_status": "unavailable",
                        "reason": "review_condition_mismatch", "verification_audit": None}
    assert "interpretation" not in exported and "interpretation_qualification" not in exported
    no_c.assert_not_called()
