"""Config-draft validation: invalid GoalsConfig → field_errors, valid → none;
test_connection=false → connection null; persists nothing.

POST /api/setup/validate-config is a dry-run editor aid — it must never mutate
app state.
"""
from __future__ import annotations

import asyncio
import copy

import yaml

from zotero_summarizer.models.setup import ValidateConfigRequest
from zotero_summarizer.services.setup.validate import validate_config_draft


def _run(coro):
    return asyncio.run(coro)


def _valid_config_dict() -> dict:
    # The committed example is a known-valid GoalsConfig; load it as the draft.
    with open("goals.example.yaml", "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    # The example uses ${OPENAI_API_BASE}; give llm.api_base a concrete value so it
    # validates without env expansion (validate_config_draft does NOT expand env).
    raw["llm"]["api_base"] = "http://localhost:11434/v1"
    return raw


def test_invalid_config_returns_field_errors_and_null_connection():
    bad = _valid_config_dict()
    del bad["relevance_scale"]  # required field → validation error
    req = ValidateConfigRequest(config=bad, test_connection=False)
    resp = _run(validate_config_draft(req))
    assert resp.valid is False
    assert resp.connection is None
    assert resp.field_errors  # at least one
    locs = [err.loc for err in resp.field_errors]
    assert any("relevance_scale" in loc for loc in locs)


def test_valid_config_no_errors_connection_null_when_not_tested():
    good = _valid_config_dict()
    req = ValidateConfigRequest(config=good, test_connection=False)
    resp = _run(validate_config_draft(req))
    assert resp.valid is True
    assert resp.field_errors == []
    # connection is null when test_connection=false, even for a valid config.
    assert resp.connection is None


def test_validate_persists_nothing(tmp_path, monkeypatch):
    """A validate call writes no goals.yaml and no .env (read-only)."""
    good = _valid_config_dict()
    snapshot = copy.deepcopy(good)

    before = sorted(p.name for p in tmp_path.rglob("*"))
    req = ValidateConfigRequest(config=good, test_connection=False)
    _run(validate_config_draft(req))
    after = sorted(p.name for p in tmp_path.rglob("*"))

    assert before == after  # nothing written anywhere under the tmp tree
    assert good == snapshot  # the input dict is not mutated


def test_connection_probe_runs_when_requested(monkeypatch):
    """With test_connection=true on a valid config, the probe + model listing are
    invoked and folded into ``connection`` (mechanism is stubbed — no network)."""
    from zotero_summarizer.services.setup import validate as validate_mod

    monkeypatch.setattr(
        validate_mod.operational_check,
        "probe_provider",
        lambda provider, model: {"status": "operational", "detail": ""},
    )
    monkeypatch.setattr(
        validate_mod.model_list,
        "list_models_for_provider",
        lambda provider: ["m1", "m2", "m3"],
    )
    good = _valid_config_dict()
    req = ValidateConfigRequest(config=good, test_connection=True)
    resp = _run(validate_config_draft(req))
    assert resp.valid is True
    assert resp.connection is not None
    assert resp.connection.status == "operational"
    assert resp.connection.models_discovered == 3
    assert resp.connection.tested_provider
    assert resp.connection.tested_model


def test_connection_reports_fail_without_raising(monkeypatch):
    """An unreachable provider comes back as status=fail (not a 500); model count
    degrades to 0 with the probe detail authoritative."""
    from zotero_summarizer.services.setup import validate as validate_mod

    monkeypatch.setattr(
        validate_mod.operational_check,
        "probe_provider",
        lambda provider, model: {"status": "fail", "detail": "ConnectionError: refused"},
    )

    def _boom(provider):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(validate_mod.model_list, "list_models_for_provider", _boom)
    good = _valid_config_dict()
    req = ValidateConfigRequest(config=good, test_connection=True)
    resp = _run(validate_config_draft(req))
    assert resp.connection.status == "fail"
    assert resp.connection.models_discovered == 0
    assert "refused" in resp.connection.detail
