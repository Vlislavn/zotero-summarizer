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
    def build(provider, model, *, verification):
        assert verification is True
        record = {"provider": provider.name, "model": model, "closed": False}
        constructed.append(record)
        def close():
            record["closed"] = True
        from tests._setup_request_events import observed_client
        return observed_client(provider, model, close=close)
    monkeypatch.setattr(oc, "build_client_for_provider", build)
    monkeypatch.setattr(validate.model_list, "list_models_for_provider", lambda _: [])
    return constructed


def test_draft_local_unknown_never_constructs(monkeypatch):
    constructed = counting_client(monkeypatch)
    response = asyncio.run(validate.validate_config_draft(
        ValidateConfigRequest(config=draft(), test_connection=True)))
    assert response.valid
    assert response.connection.status != "operational"
    assert response.connection.reason_code == "effective_envelope_unknown"
    assert response.connection.hardware_safe == "unknown"
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
    assert len(constructed) == 1 and constructed[0]["closed"] is True
    assert response.connection.observed_operational is True
    assert response.connection.hardware_safe == "not_applicable"


def _timeout_diagnostic(monkeypatch, *, local=False, owned=False):
    from zotero_summarizer.models.config import GoalsConfig
    from zotero_summarizer.models.providers import StageModelConfig

    raw = draft()
    raw["llm"]["api_base"] = "http://localhost:11434/v1" if local else "https://synthetic.invalid/v1"
    routes = GoalsConfig.model_validate(raw).llm_routing
    routes.deep_review = StageModelConfig(model="separate-hosted-model")
    waits, probes = [], []

    async def controlled_wait(work, *, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            work.close()  # no dispatch: this discriminator has no orphaned worker
            raise asyncio.TimeoutError
        return await work

    def probe(provider, model):
        probes.append((provider.name, model))
        return {"status": "operational", "detail": ""}

    monkeypatch.setattr(oc.asyncio, "wait_for", controlled_wait)
    monkeypatch.setattr(oc, "probe_provider", probe)
    result = asyncio.run(oc.check_routing_stages(routes, **({"should_stop": lambda: False} if owned else {})))
    return result, probes, waits, routes


def test_nonsetup_hosted_timeout_does_not_suppress_next_unique_probe(monkeypatch):
    result, probes, waits, routes = _timeout_diagnostic(monkeypatch)
    assert [row["status"] for row in result["stages"]] == ["fail", "fail", "operational"]
    assert probes == [(routes.providers[0].name, "separate-hosted-model")]
    assert waits == [oc._PROBE_TIMEOUT_SECS, oc._PROBE_TIMEOUT_SECS]


@pytest.mark.parametrize("local,owned", [(True, False), (False, True)])
def test_local_or_setup_owned_timeout_still_fences_next_probe(monkeypatch, local, owned):
    result, probes, waits, _routes = _timeout_diagnostic(monkeypatch, local=local, owned=owned)
    assert [row["status"] for row in result["stages"]] == ["fail", "fail", "fail"]
    assert [row["reason_code"] for row in result["stages"]] == ["waiter_timeout", "waiter_timeout", "batch_abandoned"]
    assert result["stages"][2]["attempted"] is False
    assert probes == []
    assert waits == [oc._LOCAL_PROBE_TIMEOUT_SECS if local else oc._PROBE_TIMEOUT_SECS]


def test_disabled_draft_explicit_test_never_constructs(monkeypatch):
    constructed = counting_client(monkeypatch)
    raw = draft()
    raw["llm_enabled"] = False
    response = asyncio.run(validate.validate_config_draft(
        ValidateConfigRequest(config=raw, test_connection=True)))
    assert response.connection.status == "disabled"
    assert response.connection.observed_operational is False
    assert response.connection.hardware_safe == "not_applicable"
    assert constructed == []


def resolved_local():
    from zotero_summarizer.models.config import GoalsConfig
    from zotero_summarizer.models.providers import resolve_stage
    return resolve_stage(GoalsConfig.model_validate(draft()).llm_routing, "deep_review")


def policy_for(resolved):
    return dict(schema_version=1, approval_reference="SYNTHETIC-not-measured",
        identity=dict(provider=resolved.provider.model_dump(mode="json"), model=resolved.model),
        effective_envelope=dict(context_tokens=1024, placement="cpu", memory_domain="host_available_bytes"),
        peak_working_set_bytes=10, reserved_system_bytes=2)


def known_observation(resolved, available=12):
    return dict(available_bytes=available, identity=policy_for(resolved)["identity"],
                effective_envelope=policy_for(resolved)["effective_envelope"])


def bind_configuration(configuration):
    from zotero_summarizer.runtime import AppContext, set_context
    set_context(AppContext(settings=configuration))
    return configuration


def write_policy(tmp_path, policy, *, approved=True):
    configuration = Settings.load(project_root=tmp_path)
    configuration.data_dir.mkdir(parents=True, exist_ok=True)
    path = configuration.data_dir / "synthetic-policy.json"
    raw = json.dumps(policy).encode()
    path.write_bytes(raw)
    return bind_configuration(replace(configuration, setup_load_approval_policy_path=path,
        setup_load_approved_policy_sha256=hashlib.sha256(raw).hexdigest() if approved else None))


@pytest.mark.parametrize("available,status,reason", [
    (12, "admitted", "policy_satisfied"), (11, "blocked", "available_memory_insufficient"),
    (None, "unknown", "telemetry_unavailable"), (-1, "error", "telemetry_failed"),
    (True, "error", "telemetry_failed"),
])
def test_synthetic_budget_reserve_is_exact(tmp_path, monkeypatch, available, status, reason):
    from zotero_summarizer.services.setup import load_admission as admission
    resolved = resolved_local()
    write_policy(tmp_path, policy_for(resolved))
    monkeypatch.setattr(admission, "_observe_executing_runtime",
                        lambda provider, model: known_observation(resolved, available))
    result = admission.assess_setup_load(resolved.provider, resolved.model)
    assert (result["status"], result["reason_code"]) == (status, reason)
    assert result["hardware_safe"] == "unknown"  # synthetic wiring, never hardware proof


def test_missing_effective_envelope_and_failed_telemetry_distinct(tmp_path, monkeypatch):
    from zotero_summarizer.services.setup import load_admission as admission
    resolved = resolved_local()
    write_policy(tmp_path, policy_for(resolved))
    def failed(provider, model):
        raise RuntimeError("synthetic telemetry failure")
    monkeypatch.setattr(admission, "_observe_executing_runtime", failed)
    result = admission.assess_setup_load(resolved.provider, resolved.model)
    assert (result["status"], result["reason_code"]) == ("error", "telemetry_failed")
    monkeypatch.setattr(admission, "_observe_executing_runtime",
                        lambda provider, model: {"available_bytes": 100000})
    result = admission.assess_setup_load(resolved.provider, resolved.model)
    assert (result["status"], result["reason_code"]) == ("unknown", "effective_envelope_unknown")


@pytest.mark.parametrize("bad", ["{", "{}", '{"version":true}', "x" * 65537])
def test_present_malformed_policy_loud_even_without_approval_binding(tmp_path, bad):
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    configuration = write_policy(tmp_path, policy_for(resolved_local()), approved=False)
    configuration.setup_load_approval_policy_path.write_text(bad)
    resolved = resolved_local()
    result = assess_setup_load(resolved.provider, resolved.model)
    assert result["status"] == "error" and result["reason_code"] == "policy_configuration_error"


def test_policy_io_failure_not_absent_unknown(tmp_path):
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    configuration = Settings.load(project_root=tmp_path)
    bind_configuration(replace(configuration,
        setup_load_approval_policy_path=configuration.data_dir / "missing"))
    resolved = resolved_local()
    result = assess_setup_load(resolved.provider, resolved.model)
    assert (result["status"], result["reason_code"]) == ("error", "policy_configuration_error")


def test_finite_policy_binding_and_production_envelope_unknown(tmp_path, monkeypatch):
    from zotero_summarizer.services.setup import load_admission as admission
    resolved = resolved_local()
    configuration = write_policy(tmp_path, policy_for(resolved), approved=False)
    path = configuration.setup_load_approval_policy_path
    raw = path.read_bytes()
    assert admission.assess_setup_load(resolved.provider, resolved.model)["reason_code"] == "policy_unbound"
    configuration = bind_configuration(replace(configuration, setup_load_approved_policy_sha256="0" * 64))
    assert admission.assess_setup_load(resolved.provider, resolved.model)["reason_code"] == "policy_binding_mismatch"
    configuration = bind_configuration(replace(configuration,
        setup_load_approved_policy_sha256=hashlib.sha256(raw).hexdigest()))
    # Production has no authoritative envelope producer, even with valid approval.
    assert admission.assess_setup_load(resolved.provider, resolved.model)["reason_code"] == "effective_envelope_unknown"
    monkeypatch.setattr(admission, "_observe_executing_runtime",
                        lambda provider, model: known_observation(resolved))
    assert admission.assess_setup_load(resolved.provider, resolved.model)["status"] == "admitted"
    assert admission.assess_setup_load(resolved.provider, "other")["reason_code"] == "policy_identity_mismatch"
    signature = admission.policy_configuration_signature(configuration)
    changed = raw.replace(b"not-measured", b"not-Measured")
    assert len(changed) == len(raw) and changed != raw
    path.write_bytes(changed)
    assert admission.policy_configuration_signature(configuration) != signature
    assert admission.assess_setup_load(resolved.provider, resolved.model)["reason_code"] == "policy_binding_mismatch"
    bind_configuration(replace(configuration, setup_load_approved_policy_sha256=hashlib.sha256(changed).hexdigest()))
    assert admission.assess_setup_load(resolved.provider, resolved.model)["status"] == "admitted"


def test_doctor_inference_unknown_and_modes_not_ready(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    constructed = counting_client(monkeypatch)
    row = doctor._llm_inference(settings)
    assert row["status"] == "needs_action" and constructed == []
    assert all(not result["attempted"] for result in row["load_receipts"])
    assert row["recovery"]["action"] == "open_ai_settings"
    rows = [doctor._row(key, "ready", "synthetic") for key in doctor._CHECKS]
    for missing in ("dry_run", "llm_inference", "ml_assets"):
        changed = [dict(row) for row in rows]
        changed[doctor._CHECKS.index(missing)]["status"] = "needs_action"
        modes = doctor._modes(settings, changed)
        assert modes["offline_ready"] == "needs_action"
        if missing != "ml_assets":
            assert modes["local_inference"] == "needs_action"
    assert doctor._modes(settings, rows)["local_inference"] == "ready"


def test_persisted_mode_readout_cannot_outlive_its_failed_dry_run(tmp_path):
    from zotero_summarizer.services._common import write_json_atomic

    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    rows = [doctor._row(key, "ready", "synthetic") for key in doctor._CHECKS]
    rows[doctor._CHECKS.index("dry_run")]["status"] = "needs_action"
    path = settings.data_dir / "setup_doctor.json"
    write_json_atomic(path, {
        "status": "needs_action", "ready": False, "checks": rows,
        "verification_revision": doctor.ADMISSION_REVISION,
        "config_signature": doctor._config_signature(settings),
        "modes": {"local_inference": "ready", "offline_ready": "ready"},
    })
    before = path.read_bytes(), path.stat().st_mtime_ns
    result = doctor.doctor_status(settings)
    assert result["ready"] is False
    assert result["modes"]["local_inference"] == result["modes"]["offline_ready"] == "needs_action"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


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
    ("reserve_bytes", -1), ("budget_bytes", "10"), ("schema_version", 1.0),
    ("peak_working_set_bytes", 0), ("reserved_system_bytes", True), ("context_tokens", True)])
def test_present_policy_strict_numeric_schema(tmp_path, monkeypatch, field, bad):
    from zotero_summarizer.services.setup import load_admission as admission
    resolved = resolved_local()
    policy = policy_for(resolved)
    # Preserve original named vectors; port only the actual file field spelling.
    field = {"version": "schema_version", "budget_bytes": "peak_working_set_bytes",
             "reserve_bytes": "reserved_system_bytes"}.get(field, field)
    if field == "context_tokens":
        policy["effective_envelope"][field] = bad
    else:
        policy[field] = bad
    write_policy(tmp_path, policy)
    monkeypatch.setattr(admission, "_observe_executing_runtime",
                        lambda provider, model: pytest.fail("Invalid policy reached telemetry"))
    result = admission.assess_setup_load(resolved.provider, resolved.model)
    assert (result["status"], result["reason_code"]) == ("error", "policy_configuration_error")


def test_non_regular_policy_is_bounded_error(tmp_path):
    import os
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    configuration = Settings.load(project_root=tmp_path)
    configuration.data_dir.mkdir()
    fifo = configuration.data_dir / "synthetic-policy.fifo"
    os.mkfifo(fifo)
    bind_configuration(replace(configuration, setup_load_approval_policy_path=fifo))
    resolved = resolved_local()
    assert assess_setup_load(resolved.provider, resolved.model)["reason_code"] == "policy_configuration_error"


def test_policy_revision_invalidates_old_ready_not_volatile_memory(tmp_path, monkeypatch):
    from zotero_summarizer.services.setup import load_admission
    configuration = Settings.load(project_root=tmp_path)
    bootstrap_phase0(configuration)
    resolved = resolved_local()
    configuration = write_policy(tmp_path, policy_for(resolved))
    signature = doctor._config_signature(configuration)
    monkeypatch.setattr(load_admission, "_observe_executing_runtime",
                        lambda provider, model: known_observation(resolved, 1))
    assert doctor._config_signature(configuration) == signature
    rows = [doctor._row(key, "ready", "synthetic") for key in doctor._CHECKS]
    from zotero_summarizer.services._common import write_json_atomic
    write_json_atomic(configuration.data_dir / "setup_doctor.json", {
        "status": "ready", "ready": True, "checks": rows,
        "verification_revision": doctor.ADMISSION_REVISION, "config_signature": signature})
    path = configuration.setup_load_approval_policy_path
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b"not-measured", b"not-Measured"))
    assert doctor.doctor_status(configuration)["ready"] is False
    path.write_bytes(raw)
    rebound = replace(configuration, setup_load_approved_policy_sha256="0" * 64)
    assert doctor.doctor_status(rebound)["ready"] is False
    monkeypatch.setattr(load_admission, "ADMISSION_REVISION", "synthetic-next-revision")
    assert doctor._config_signature(configuration) != signature
    assert doctor.doctor_status(configuration)["ready"] is False


def test_nested_machine_receipts_redact_secrets(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    bootstrap_phase0(settings)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    row = doctor._row("llm_inference", "needs_action", "synthetic", "synthetic-secret")
    row["load_receipts"] = [{"detail": "synthetic-secret", "admission": {"detail": "synthetic-secret"}}]
    rows = [row]
    doctor._redact_details(settings, rows)
    assert "synthetic-secret" not in json.dumps(rows)
    assert rows[0]["load_receipts"][0]["detail"] != "synthetic-secret"


@pytest.mark.parametrize("field,value,reason", [
    ("context_tokens", 2048, "policy_envelope_mismatch"),
    ("placement", "gpu", "policy_envelope_mismatch"),
    ("memory_domain", "device:0", "policy_envelope_mismatch"),
    ("identity", {"provider": {}, "model": "other"}, "runtime_identity_mismatch"),
])
def test_runtime_evidence_must_match_exact_envelope_and_identity(tmp_path, monkeypatch, field, value, reason):
    from zotero_summarizer.services.setup import load_admission as admission
    resolved = resolved_local()
    write_policy(tmp_path, policy_for(resolved))
    observed = known_observation(resolved)
    if field == "identity":
        observed[field] = value
    else:
        observed["effective_envelope"][field] = value
    monkeypatch.setattr(admission, "_observe_executing_runtime", lambda provider, model: observed)
    result = admission.assess_setup_load(resolved.provider, resolved.model)
    assert (result["status"], result["reason_code"]) == ("blocked", reason)
    changed = resolved.provider.model_copy(update={"num_ctx": 2048})
    assert admission.assess_setup_load(changed, resolved.model)["reason_code"] == "policy_identity_mismatch"


def test_every_admission_samples_again_without_writing_policy(tmp_path, monkeypatch):
    from zotero_summarizer.services.setup import load_admission as admission
    resolved = resolved_local()
    configuration = write_policy(tmp_path, policy_for(resolved))
    path = configuration.setup_load_approval_policy_path
    before = path.read_bytes(), path.stat().st_mtime_ns
    samples, calls = iter([12, 11, None]), []
    def observe(provider, model):
        calls.append((provider.model_dump(mode="json"), model))
        return known_observation(resolved, next(samples))
    monkeypatch.setattr(admission, "_observe_executing_runtime", observe)
    assert [admission.assess_setup_load(resolved.provider, resolved.model)["status"]
            for _ in range(3)] == ["admitted", "blocked", "unknown"]
    assert calls == [(resolved.provider.model_dump(mode="json"), resolved.model)] * 3
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("kind", ["directory", "symlink", "parent_symlink", "outside", "unreadable"])
def test_policy_path_rejects_unsafe_actual_files(tmp_path, kind):
    from zotero_summarizer.services.setup.load_admission import assess_setup_load
    resolved = resolved_local()
    configuration = write_policy(tmp_path, policy_for(resolved), approved=False)
    path = configuration.setup_load_approval_policy_path
    if kind == "directory":
        path = configuration.data_dir
    elif kind == "symlink":
        target = path
        path = configuration.data_dir / "linked.json"
        path.symlink_to(target)
    elif kind == "parent_symlink":
        directory = configuration.data_dir / "linked"
        directory.symlink_to(configuration.data_dir, target_is_directory=True)
        path = directory / path.name
    elif kind == "outside":
        path = tmp_path / "outside.json"
        path.write_bytes(configuration.setup_load_approval_policy_path.read_bytes())
    else:
        path.chmod(0)
    bind_configuration(replace(configuration, setup_load_approval_policy_path=path))
    try:
        result = assess_setup_load(resolved.provider, resolved.model)
        assert (result["status"], result["reason_code"]) == ("error", "policy_configuration_error")
    finally:
        if kind == "unreadable":
            path.chmod(0o600)


@pytest.mark.parametrize("configured", [False, True])
def test_local_refusal_has_no_client_credentials_or_file_effects(tmp_path, monkeypatch, configured):
    from zotero_summarizer.services.llm import factory
    from zotero_summarizer.api.errors import APIError
    configuration = Settings.load(project_root=tmp_path)
    if configured:
        configuration = replace(configuration, setup_load_approval_policy_path=configuration.data_dir / "missing")
    bind_configuration(configuration)
    def forbidden(*args, **kwargs):
        pytest.fail("Refusal reached a credential, client or telemetry effect")
    from zotero_summarizer.services.setup import load_admission as admission
    monkeypatch.setattr(admission, "_observe_executing_runtime", forbidden)
    monkeypatch.setattr(factory, "resolve_api_key", forbidden)
    monkeypatch.setattr(factory, "build_llm", forbidden)
    before = sorted(tmp_path.rglob("*"))
    resolved = resolved_local()
    with pytest.raises(APIError) as refused:
        factory.build_client_for_provider(resolved.provider, resolved.model, verification=True)
    assert refused.value.error == ("policy_configuration_error" if configured else "effective_envelope_unknown")
    assert sorted(tmp_path.rglob("*")) == before
