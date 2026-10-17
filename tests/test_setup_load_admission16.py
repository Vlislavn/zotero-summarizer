"""Setup load boundary: synthetic identities only, never an actual endpoint."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import pytest
from types import SimpleNamespace

import yaml

from zotero_summarizer.cli import _setup
from zotero_summarizer.models.setup import ValidateConfigRequest
from zotero_summarizer.services.llm import operational_check as oc
from zotero_summarizer.services.setup import doctor, validate
from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
from zotero_summarizer.settings import Settings


def draft():
    with open("goals.example.yaml", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    raw["research_goals"] = ["Synthetic research goal"]
    raw["llm"]["api_base"] = "http://localhost:11434/v1"
    return raw


def counting_client(monkeypatch):
    constructed = []
    monkeypatch.setattr(oc, "build_client_for_provider", lambda p, m: (
        constructed.append((p.name, m)) or SimpleNamespace(prompt=lambda _: "ok")))
    monkeypatch.setattr(validate.model_list, "list_models_for_provider", lambda _: [])
    return constructed


def test_draft_local_unknown_never_constructs(monkeypatch):
    constructed = counting_client(monkeypatch)
    response = asyncio.run(validate.validate_config_draft(
        ValidateConfigRequest(config=draft(), test_connection=True)))
    assert response.valid
    assert response.connection.status != "operational"
    assert "policy" in response.connection.detail.lower()
    assert constructed == []


def test_direct_cli_hosted_name_loopback_cannot_bypass(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    constructed = counting_client(monkeypatch)
    answers = iter(["openai", "http://localhost:11434/v1", "OPENAI_API_KEY", "synthetic"])
    monkeypatch.setattr(_setup, "_prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(_setup, "_confirm", lambda *a, **k: True)
    _setup._step_provider(settings)
    assert "synthetic" in settings.config_path.read_text()
    assert constructed == []


def test_doctor_dry_run_local_unknown_never_constructs(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.services.triage import summarization
    constructed = []
    monkeypatch.setattr(factory, "build_client_for_stage", lambda r: constructed.append(r))
    monkeypatch.setattr(summarization, "run_abstract_dry", lambda *a: SimpleNamespace(
        reading_priority="could", relevance_score=1))
    row = doctor._dry_run(settings)
    assert row["status"] == "needs_action"
    assert row["recovery"]["action"] == "open_ai_settings"
    assert constructed == []


def test_hosted_draft_remains_operational(monkeypatch):
    constructed = counting_client(monkeypatch)
    raw = draft()
    raw["llm"]["api_base"] = "https://synthetic.invalid/v1"
    response = asyncio.run(validate.validate_config_draft(
        ValidateConfigRequest(config=raw, test_connection=True)))
    assert response.connection.status == "operational"
    assert len(constructed) == 1


def test_disabled_draft_explicit_test_never_constructs(monkeypatch):
    constructed = counting_client(monkeypatch)
    raw = draft()
    raw["llm_enabled"] = False
    response = asyncio.run(validate.validate_config_draft(
        ValidateConfigRequest(config=raw, test_connection=True)))
    assert response.connection is None
    assert constructed == []


def resolved_local():
    from zotero_summarizer.models.config import GoalsConfig
    from zotero_summarizer.models.providers import resolve_stage
    return resolve_stage(GoalsConfig.model_validate(draft()).llm_routing, "deep_review")


def policy_for(resolved):
    from zotero_summarizer.services.setup.load_admission import load_identity
    return dict(version=1, policy_ref="SYNTHETIC-not-measured", identity=load_identity(resolved),
        envelope=dict(context_tokens=1024, placement="cpu", memory_domain="host_available_bytes"),
        budget_bytes=10, reserve_bytes=2)


def known_facts(available=12):
    return lambda: dict(available_bytes=available, envelope=dict(
        context_tokens=1024, placement="cpu", memory_domain="host_available_bytes"))


@pytest.mark.parametrize("available,status,reason", [
    (12, "admitted", "policy_satisfied"), (11, "blocked", "available_memory_insufficient"),
    (None, "unknown", "telemetry_unavailable"), (-1, "error", "telemetry_failed"),
    (True, "error", "telemetry_failed"),
])
def test_synthetic_budget_reserve_is_exact(available, status, reason):
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    resolved = resolved_local()
    result = assess_setup_load(None, resolved, policy=policy_for(resolved), facts=known_facts(available))
    assert (result["status"], result["reason_code"]) == (status, reason)


def test_missing_effective_envelope_and_failed_telemetry_distinct():
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    resolved = resolved_local()
    def failed():
        raise RuntimeError("synthetic telemetry failure")
    result = assess_setup_load(None, resolved, policy=policy_for(resolved), facts=failed)
    assert result["reason_code"] == "telemetry_failed"
    result = assess_setup_load(None, resolved, policy=policy_for(resolved),
                              facts=lambda: {"available_bytes": 100000})
    assert result["reason_code"] == "effective_envelope_unknown"


@pytest.mark.parametrize("bad", ["{", "{}", '{"version":true}', "x" * 65537])
def test_present_malformed_policy_loud_even_without_approval_binding(tmp_path, bad):
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    path = tmp_path / "synthetic-policy.json"
    path.write_text(bad)
    settings = replace(Settings.load(project_root=tmp_path), setup_load_policy_path=path)
    result = assess_setup_load(settings, resolved_local())
    assert result["status"] == "error" and result["reason_code"] == "policy_configuration_error"


def test_policy_io_failure_not_absent_unknown(tmp_path):
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    settings = replace(Settings.load(project_root=tmp_path), setup_load_policy_path=tmp_path / "missing")
    assert assess_setup_load(settings, resolved_local())["reason_code"] == "policy_configuration_error"


def test_finite_policy_binding_and_production_envelope_unknown(tmp_path, monkeypatch):
    from zotero_summarizer.services.setup import load_admission as admission
    monkeypatch.setattr(admission, "fresh_load_facts", lambda: {"available_bytes": 12, "envelope": None})
    resolved = resolved_local()
    raw = json.dumps(policy_for(resolved)).encode()
    path = tmp_path / "synthetic-policy.json"
    path.write_bytes(raw)
    settings = replace(Settings.load(project_root=tmp_path), setup_load_policy_path=path)
    assert admission.assess_setup_load(settings, resolved)["reason_code"] == "policy_unbound"
    settings = replace(settings, setup_load_policy_sha256="0" * 64)
    assert admission.assess_setup_load(settings, resolved)["reason_code"] == "policy_binding_mismatch"
    settings = replace(settings, setup_load_policy_sha256=hashlib.sha256(raw).hexdigest())
    assert admission.assess_setup_load(settings, resolved)["reason_code"] == "effective_envelope_unknown"
    result = admission.assess_setup_load(settings, resolved, facts=known_facts())
    assert result["status"] == "admitted"  # SYNTHETIC ONLY, not adequacy evidence
    changed = resolved.model_copy(update={"model": "other"})
    assert admission.assess_setup_load(settings, changed, facts=known_facts())["reason_code"] == "policy_identity_mismatch"
    signature = admission.policy_signature(settings)
    path.write_bytes(raw + b"\n")
    assert admission.policy_signature(settings) != signature


def test_doctor_inference_unknown_and_modes_not_ready(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    constructed = counting_client(monkeypatch)
    row = doctor._llm_inference(settings)
    assert row["status"] == "needs_action" and constructed == []
    assert all(not result["attempted"] for result in row["load_receipts"])
    assert row["recovery"]["action"] == "open_ai_settings"
    rows = [doctor._row(key, "ready", "synthetic") for key in doctor._CHECKS]
    rows[doctor._CHECKS.index("dry_run")]["status"] = "needs_action"
    assert doctor._modes(settings, rows)["local_inference"] == "needs_action"


def test_cli_inference_confirmation_default_no_and_saved(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    answers = iter(["anthropic", "TEST_API_KEY", "synthetic"])
    monkeypatch.setattr(_setup, "_prompt", lambda *a, **k: next(answers))
    seen = []
    monkeypatch.setattr(_setup, "_confirm", lambda label, *, default: seen.append((label, default)) or False)
    _setup._step_provider(settings)
    assert seen == [("Run real inference now (may load the configured model)?", False)]
    assert "synthetic" in settings.config_path.read_text()


def test_actual_loaded_sources_are_exclusive_c_bytes(tmp_path):
    import pathlib
    import sys
    from zotero_summarizer.api.routes import setup as setup_route
    from zotero_summarizer.services.setup import load_admission, load_attempt
    root = pathlib.Path(__file__).resolve().parents[1]
    modules = [doctor, validate, oc, _setup, setup_route, load_admission, load_attempt,
               sys.modules[Settings.__module__], sys.modules[ValidateConfigRequest.__module__]]
    bindings = {}
    for module in modules:
        source = pathlib.Path(module.__file__).resolve()
        assert source.is_relative_to(root)
        bindings[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
    (tmp_path / "actual-loaded-sources.json").write_text(json.dumps(bindings, indent=2))
    assert len(bindings) == 9


@pytest.mark.parametrize("field,bad", [("version", True), ("budget_bytes", True),
                                       ("reserve_bytes", -1), ("budget_bytes", "10")])
def test_present_policy_strict_numeric_schema(field, bad):
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    resolved = resolved_local()
    policy = policy_for(resolved)
    policy[field] = bad
    result = assess_setup_load(None, resolved, policy=policy, facts=known_facts())
    assert result["reason_code"] == "policy_configuration_error"


def test_non_regular_policy_is_bounded_error(tmp_path):
    import os
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    fifo = tmp_path / "synthetic-policy.fifo"
    os.mkfifo(fifo)
    settings = replace(Settings.load(project_root=tmp_path), setup_load_policy_path=fifo)
    assert assess_setup_load(settings, resolved_local())["reason_code"] == "policy_configuration_error"


def test_policy_revision_invalidates_old_ready_not_volatile_memory(tmp_path, monkeypatch):
    from zotero_summarizer.services.setup import load_admission
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    signature = doctor._config_signature(settings)
    monkeypatch.setattr(load_admission, "fresh_load_facts", lambda: {"available_bytes": 1})
    assert doctor._config_signature(settings) == signature
    monkeypatch.setattr(load_admission, "ADMISSION_REVISION", "synthetic-next-revision")
    assert doctor._config_signature(settings) != signature


def test_nested_machine_receipts_redact_secrets(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    row = doctor._row("llm_inference", "needs_action", "synthetic", "synthetic-secret")
    row["load_receipts"] = [{"detail": "synthetic-secret", "admission": {"detail": "synthetic-secret"}}]
    doctor._redact_details(settings, [row])
    assert "synthetic-secret" not in json.dumps(row)
