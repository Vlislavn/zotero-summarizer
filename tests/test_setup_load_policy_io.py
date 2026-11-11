"""Actual policy file boundaries; no SDK, model or physical-capacity acceptance."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_setup_load_admission16 import (
    bind_configuration, known_observation, policy_for, resolved_local, write_policy,
)
from zotero_summarizer.services.setup import _load_policy, load_admission


@pytest.mark.parametrize("bad", [
    b'[]', b'null', b'"policy"', b'\xff',
    b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e9999}',
])
def test_actual_json_refuses_before_observation_even_when_approved(tmp_path, monkeypatch, bad):
    resolved = resolved_local()
    cfg = write_policy(tmp_path, policy_for(resolved))
    cfg.setup_load_approval_policy_path.write_bytes(bad)
    bind_configuration(replace(cfg, setup_load_approved_policy_sha256=hashlib.sha256(bad).hexdigest()))
    monkeypatch.setattr(load_admission, "_observe_executing_runtime",
                        lambda *args: pytest.fail("Invalid policy reached runtime"))
    result = load_admission.assess_setup_load(resolved.provider, resolved.model)
    assert (result["status"], result["reason_code"]) == ("error", "policy_configuration_error")


def test_exact_policy_size_boundary_and_relative_path_are_real_reads(tmp_path, monkeypatch):
    resolved = resolved_local()
    cfg = write_policy(tmp_path, policy_for(resolved))
    raw = cfg.setup_load_approval_policy_path.read_bytes()
    raw += b" " * (_load_policy.MAX_POLICY_BYTES - len(raw))
    cfg.setup_load_approval_policy_path.write_bytes(raw)
    bind_configuration(replace(cfg, setup_load_approval_policy_path=Path(cfg.setup_load_approval_policy_path.name),
                               setup_load_approved_policy_sha256=hashlib.sha256(raw).hexdigest()))
    monkeypatch.setattr(load_admission, "_observe_executing_runtime",
                        lambda *args: known_observation(resolved))
    assert load_admission.assess_setup_load(resolved.provider, resolved.model)["status"] == "admitted"
    cfg.setup_load_approval_policy_path.write_bytes(raw + b" ")
    monkeypatch.setattr(_load_policy.os, "read", lambda *args: pytest.fail("Oversized file was read"))
    assert load_admission.assess_setup_load(resolved.provider, resolved.model)["reason_code"] == "policy_configuration_error"


def test_policy_replacement_between_stat_and_open_refuses(tmp_path, monkeypatch):
    resolved = resolved_local()
    cfg = write_policy(tmp_path, policy_for(resolved))
    path = cfg.setup_load_approval_policy_path
    replacement = path.with_name("replacement.json")
    replacement.write_bytes(path.read_bytes())
    original_open = _load_policy.os.open
    replaced = []

    def controlled_open(name, flags, **kwargs):
        if name == path.name:
            replacement.replace(path)
            replaced.append(True)
        return original_open(name, flags, **kwargs)

    monkeypatch.setattr(_load_policy.os, "open", controlled_open)
    result = load_admission.assess_setup_load(resolved.provider, resolved.model)
    assert replaced == [True]
    assert (result["status"], result["reason_code"]) == ("error", "policy_configuration_error")


def test_policy_change_during_actual_read_refuses_and_closes_descriptors(tmp_path, monkeypatch):
    resolved = resolved_local()
    cfg = write_policy(tmp_path, policy_for(resolved))
    path = cfg.setup_load_approval_policy_path
    original_read, original_open, original_close = _load_policy.os.read, _load_policy.os.open, _load_policy.os.close
    opened, closed = [], []

    def controlled_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def controlled_read(descriptor, limit):
        content = original_read(descriptor, limit)
        path.write_bytes(content + b" ")
        return content

    def controlled_close(descriptor):
        original_close(descriptor)
        closed.append(descriptor)

    monkeypatch.setattr(_load_policy.os, "open", controlled_open)
    monkeypatch.setattr(_load_policy.os, "read", controlled_read)
    monkeypatch.setattr(_load_policy.os, "close", controlled_close)
    result = load_admission.assess_setup_load(resolved.provider, resolved.model)
    assert (result["status"], result["reason_code"]) == ("error", "policy_configuration_error")
    assert len(opened) == 2 and closed == list(reversed(opened))


@pytest.mark.parametrize("path", ["..", "../outside.json", "."])
def test_relative_traversal_or_directory_refuses_without_file_effects(tmp_path, path):
    resolved = resolved_local()
    cfg = write_policy(tmp_path, policy_for(resolved))
    bind_configuration(replace(cfg, setup_load_approval_policy_path=Path(path)))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = load_admission.assess_setup_load(resolved.provider, resolved.model)
    assert (result["status"], result["reason_code"]) == ("error", "policy_configuration_error")
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_hosted_admission_does_not_read_policy_or_observe_runtime(tmp_path, monkeypatch):
    resolved = resolved_local()
    write_policy(tmp_path, policy_for(resolved))
    raw = resolved.provider.model_dump(mode="json")
    raw["base_url"] = "https://synthetic.invalid/v1"
    hosted = type(resolved.provider).model_validate(raw)
    assert hosted.is_local is False
    monkeypatch.setattr(load_admission, "read_approval_policy", lambda *args: pytest.fail("Hosted policy read"))
    monkeypatch.setattr(load_admission, "_observe_executing_runtime", lambda *args: pytest.fail("Hosted runtime read"))
    result = load_admission.assess_setup_load(hosted, resolved.model)
    assert (result["status"], result["reason_code"], result["hardware_safe"]) == (
        "not_applicable", "hosted", "not_applicable")
    assert json.loads(json.dumps(result))["identity"]["provider"]["base_url"] == "https://synthetic.invalid/v1"
