"""Narrow cache identity controls; authored tests are not scientific acceptance."""
import builtins
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_interpretation_binding import C, _assert_base_only, _native_entry, config
from tests.test_interpretation_producer import _card
from tests.test_review_cache import _isolate_cache
from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library import _digest_audit, _review_cache, _review_identity
from zotero_summarizer.services.library.review_fleet import propose


@pytest.mark.parametrize("marker", ["absent", "unknown", None, "paper-digest-v1"])
def test_stored_condition_refuses_before_optional_import_schema_contract_or_policy(tmp_path, config, monkeypatch, marker):
    entry = _native_entry(tmp_path, config)
    stored = entry["review_identity"]
    if marker == "absent":
        del stored["interpretation_schema"]
    else:
        stored["interpretation_schema"] = marker
    _review_cache._write_one("ITEM", entry)
    path = _review_cache._cache_path()
    before = path.read_bytes(), path.stat().st_mtime_ns
    no_optional = Mock(side_effect=AssertionError("condition refusal must precede optional work"))
    original_import = builtins.__import__

    def no_c_import(name, *args, **kwargs):
        assert name != "zotero_summarizer.models.interpretation"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_c_import)
    monkeypatch.setattr(PaperDigest, "model_json_schema", no_optional)
    monkeypatch.setattr(_review_identity, "state", no_optional)
    monkeypatch.setattr(_review_identity, "_generation_sources", no_optional)
    monkeypatch.setattr(_digest_audit, "_contract_hashes", no_optional)
    monkeypatch.setattr(_review_cache, "_project_review", no_optional)
    assert _review_cache.get_current_review("ITEM", interpretation_schema=C) is None
    assert not _review_cache.review_is_current(entry, "ITEM", interpretation_schema=C)
    with pytest.raises(ValueError, match="review_condition_mismatch"):
        _review_identity.current_review_identity("ITEM", stored, interpretation_schema=C)
    with pytest.raises(ValueError, match="review_condition_mismatch"):
        _digest_audit.bind_cached_audit(entry, interpretation_schema=C)
    assert _digest_audit.cached_audit_status(entry, interpretation_schema=C) == ("unavailable", "review_condition_mismatch")
    no_optional.assert_not_called()
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("selection", ["unknown", "", "paper-digest-v1", False, 0])
def test_unknown_code_selection_refuses_before_cache_schema_or_contract_io(config, monkeypatch, selection):
    no_io = Mock(side_effect=AssertionError("invalid selection must not perform IO"))
    monkeypatch.setattr(_review_cache, "_read_all", no_io)
    monkeypatch.setattr(PaperDigest, "model_json_schema", no_io)
    monkeypatch.setattr(_review_identity, "_generation_sources", no_io)
    monkeypatch.setattr(_review_identity, "state", no_io)
    assert _review_cache.get_current_review("ITEM", interpretation_schema=selection) is None
    with pytest.raises(ValueError, match="review_condition_mismatch"):
        _review_identity.build_review_identity(config=config, pdf_path="", source_kind="override",
                                               focus_prompt="", interpretation_schema=selection)
    with pytest.raises(ValueError, match="review_condition_mismatch"):
        _review_identity.current_review_identity("ITEM", {"interpretation_schema": C}, interpretation_schema=selection)
    with pytest.raises(ValueError, match="review_condition_mismatch"):
        _digest_audit._contract_hashes(interpretation_schema=selection)
    no_io.assert_not_called()


@pytest.mark.parametrize("projected_value", [False, 0], ids=["real_false", "type_changed_zero"])
@pytest.mark.parametrize("assessed_goals", [False, True], ids=["flagged_policy", "unflagged_policy"])
def test_current_basis_binding_distinguishes_false_from_zero_through_one_raw_read(tmp_path, config, monkeypatch, projected_value, assessed_goals):
    card = _card()
    card["items"][0]["basis_refs"].extend(["/parameters", "/parameters/external_validation"])
    entry = _native_entry(tmp_path, config, card=card)
    if assessed_goals:
        entry["goal_summaries"] = [{"retrieval_state": "miss", "relevant": False}]
    binding = entry["verification_audit"]["interpretation_bindings"][0]
    assert entry["digest"]["parameters"]["external_validation"] is False
    assert binding["basis_sha256"]["/parameters/external_validation"] == _digest_audit.canonical_hash(False)
    assert _digest_audit.canonical_hash(False) != _digest_audit.canonical_hash(0)
    assert binding["matches_cached_basis"]["/parameters/external_validation"] is True
    _review_cache._write_one("ITEM", entry)
    path = _review_cache._cache_path()
    before = path.read_bytes(), path.stat().st_mtime_ns
    original_policy = propose.apply_reading_policy

    def changed(*args):
        digest, raw, flags = original_policy(*args)
        projected = deepcopy(digest)
        projected["parameters"]["external_validation"] = projected_value
        if assessed_goals:
            assert flags == [] and projected == entry["digest"]
        return projected, raw, flags

    policy = Mock(side_effect=changed)
    read = Mock(wraps=_review_cache._read_all)
    monkeypatch.setattr(propose, "apply_reading_policy", policy)
    monkeypatch.setattr(_review_cache, "_read_all", read)
    view = _review_cache.get_current_review("ITEM", interpretation_schema=C)
    assert read.call_count == policy.call_count == 1
    assert read.call_args.kwargs == {"readonly": True}
    assert type(view["digest"]["parameters"]["external_validation"]) is type(projected_value)
    qualification = view["interpretation_qualification"]
    matches = qualification["item_matches"][0]
    assert matches["finding_matches"] is True
    if projected_value is False:
        assert qualification["status"] == "available" and qualification["reason"] == ""
        assert all(matches["basis_matches"].values())
        assert view["interpretation"] == entry["interpretation"]
    else:
        _assert_base_only(view)
        assert qualification["reason"] == "interpretation_reference_changed"
        assert matches["basis_matches"]["/parameters"] is False
        assert matches["basis_matches"]["/parameters/external_validation"] is False
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_verification_to_cache_binding_also_distinguishes_false_from_zero(tmp_path, config):
    card = _card()
    card["items"][0]["basis_refs"].extend(["/parameters", "/parameters/external_validation"])
    entry = _native_entry(tmp_path, config, card=card)
    entry["digest"]["parameters"]["external_validation"] = 0
    rebound = _digest_audit.bind_cached_audit(entry, interpretation_schema=C)
    binding = rebound["verification_audit"]["interpretation_bindings"][0]
    assert binding["matches_cached_finding"] is True
    assert binding["matches_cached_basis"]["/parameters"] is False
    assert binding["matches_cached_basis"]["/parameters/external_validation"] is False
    _review_cache._write_one("ITEM", rebound)
    with pytest.raises(ValueError, match="Invalid verified base audit"):
        _review_cache.get_current_review("ITEM", interpretation_schema=C)


def test_c_only_contract_drift_withholds_card_but_preserves_current_intact_base(tmp_path, config, monkeypatch):
    entry = _native_entry(tmp_path, config)
    _review_cache._write_one("ITEM", entry)
    path = _review_cache._cache_path()
    before = path.read_bytes(), path.stat().st_mtime_ns
    original_contracts = _digest_audit._contract_hashes

    def changed_contracts(*, interpretation_schema=None):
        contracts = original_contracts(interpretation_schema=interpretation_schema)
        if interpretation_schema == C:
            contracts["interpretation_contract_sha256"] = "c" * 64
        return contracts

    contracts = Mock(side_effect=changed_contracts)
    monkeypatch.setattr(_digest_audit, "_contract_hashes", contracts)
    assert _review_cache.review_is_current(entry, "ITEM", interpretation_schema=C)
    view = _review_cache.get_current_review("ITEM", interpretation_schema=C)
    _assert_base_only(view)
    assert view["interpretation_qualification"]["reason"] == "interpretation_contract_changed"
    assert view["digest"]["tldr"] == entry["digest"]["tldr"]
    assert contracts.call_count == 2
    assert contracts.call_args_list[0].kwargs == {}
    assert contracts.call_args_list[1].kwargs == {"interpretation_schema": C}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert _review_cache._read_all(readonly=True)["ITEM"] == entry


@pytest.mark.parametrize("component", ["extraction", "verifier"])
def test_base_contract_drift_never_becomes_optional_only_failure(tmp_path, config, monkeypatch, component):
    entry = _native_entry(tmp_path, config)
    _review_cache._write_one("ITEM", entry)
    before = _review_cache._cache_path().read_bytes()
    contracts = _digest_audit._contract_hashes()
    contracts[component + "_contract_sha256"] = "c" * 64
    monkeypatch.setattr(_digest_audit, "_contract_hashes", Mock(return_value=contracts))
    no_projection = Mock(side_effect=AssertionError("stale base must not reach policy or C qualification"))
    monkeypatch.setattr(_review_cache, "_project_review", no_projection)
    monkeypatch.setattr(_digest_audit, "qualify_interpretation", no_projection)
    assert _review_cache.get_current_review("ITEM", interpretation_schema=C) is None
    no_projection.assert_not_called()
    assert _review_cache._cache_path().read_bytes() == before


@pytest.mark.parametrize("defect", ["generation_stale", "generation_missing", "source_stale", "source_null",
                                  "source_hash_missing", "source_path_null", "source_missing", "identity_null", "base_null"])
def test_stale_missing_or_malformed_source_and_base_identity_is_never_forgiven(tmp_path, config, monkeypatch, defect):
    entry = _native_entry(tmp_path, config)
    identity = entry["review_identity"]
    if defect == "generation_stale":
        identity["generation_sha256"] = "g" * 64
    elif defect == "generation_missing":
        del identity["generation_sha256"]
    elif defect == "source_stale":
        identity["source_sha256"] = "s" * 64
    elif defect == "source_null":
        identity["source_sha256"] = None
    elif defect == "source_hash_missing":
        del identity["source_sha256"]
    elif defect == "source_path_null":
        identity["source_path"] = None
    elif defect == "source_missing":
        Path(identity["source_path"]).unlink()
    elif defect == "identity_null":
        entry["review_identity"] = None
    else:
        entry["digest"] = None
    _review_cache._write_one("ITEM", entry)
    path = _review_cache._cache_path()
    before = path.read_bytes(), path.stat().st_mtime_ns
    no_projection = Mock(side_effect=AssertionError("invalid raw identity must not reach policy or C qualification"))
    monkeypatch.setattr(_review_cache, "_project_review", no_projection)
    monkeypatch.setattr(_digest_audit, "qualify_interpretation", no_projection)
    assert _review_cache.get_current_review("ITEM", interpretation_schema=C) is None
    no_projection.assert_not_called()
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("stage", ["verified", "cached"])
def test_noncanonical_base_types_raise_even_with_resealed_integrity(tmp_path, config, monkeypatch, stage):
    entry = _native_entry(tmp_path, config)
    audit = entry["verification_audit"]
    digest = audit["verified_digest"] if stage == "verified" else entry["digest"]
    digest["parameters"]["external_validation"] = 0
    audit["identity"][stage + "_digest_sha256"] = _digest_audit.canonical_hash(digest)
    audit["identity"]["audit_sha256"] = _digest_audit._payload_hash(audit)
    _review_cache._write_one("ITEM", entry)
    path = _review_cache._cache_path()
    before = path.read_bytes(), path.stat().st_mtime_ns
    no_projection = Mock(side_effect=AssertionError("malformed base must not reach policy or C qualification"))
    monkeypatch.setattr(_review_cache, "_project_review", no_projection)
    monkeypatch.setattr(_digest_audit, "qualify_interpretation", no_projection)
    with pytest.raises(ValueError, match="Invalid verified base audit"):
        _review_cache.get_current_review("ITEM", interpretation_schema=C)
    no_projection.assert_not_called()
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
