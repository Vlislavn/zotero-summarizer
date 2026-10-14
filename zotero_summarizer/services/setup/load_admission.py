"""Setup-only admission; hardware profiles are independent static advice."""
from __future__ import annotations

import hashlib
import os
import stat
from pydantic import BaseModel, ConfigDict, Field

from zotero_summarizer.models.providers import ResolvedStage
from zotero_summarizer.settings import Settings

ADMISSION_REVISION = "setup-load16-v1"
_POLICY_MAX_BYTES = 65536


class LoadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context_tokens: int = Field(gt=0)
    placement: str = Field(min_length=1)
    memory_domain: str = Field(min_length=1)


class LoadPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1, le=1)
    policy_ref: str = Field(min_length=1)
    identity: dict
    envelope: LoadEnvelope
    budget_bytes: int = Field(gt=0)
    reserve_bytes: int = Field(ge=0)


def load_identity(resolved: ResolvedStage) -> dict:
    """Full captured routing options; repeated stages share this request identity."""
    return {"provider": resolved.provider.model_dump(mode="json"), "model": resolved.model}


def policy_bytes(settings: Settings) -> bytes | None:
    path = settings.setup_load_policy_path
    if path is None:
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("setup load policy must be a regular finite file")
        raw = stream.read(_POLICY_MAX_BYTES + 1)
    if len(raw) > _POLICY_MAX_BYTES:
        raise ValueError("setup load policy exceeds 65536 bytes")
    return raw


def policy_signature(settings: Settings) -> str:
    """Bind readiness to policy bytes/revision, never volatile memory facts."""
    try:
        raw = policy_bytes(settings)
        content = hashlib.sha256(raw).hexdigest() if raw is not None else "absent"
    except (OSError, ValueError) as exc:
        content = f"policy_read_error:{type(exc).__name__}"
    return repr((ADMISSION_REVISION, settings.setup_load_policy_path,
                 settings.setup_load_policy_sha256, content))


def _read_policy(settings: Settings) -> tuple[LoadPolicy | None, str]:
    raw = policy_bytes(settings)
    if raw is None:
        return None, "policy_missing"
    # Parse before checking the independent binding: malformed presence is an error.
    policy = LoadPolicy.model_validate_json(raw)
    expected = settings.setup_load_policy_sha256
    if expected is None:
        return None, "policy_unbound"
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("setup load policy expected SHA256 must be lowercase hex")
    if hashlib.sha256(raw).hexdigest() != expected:
        return None, "policy_binding_mismatch"
    return policy, "policy_bound"


def fresh_load_facts() -> dict:
    import psutil

    memory = psutil.virtual_memory()
    # No inspected client API evidences effective runtime context/placement.
    return {"available_bytes": memory.available, "envelope": None}


def _assessment(status: str, reason: str, resolved: ResolvedStage, **facts) -> dict:
    return {"status": status, "reason_code": reason, "identity": load_identity(resolved),
            "stage": resolved.stage, **facts}


def assess_setup_load(settings: Settings, resolved: ResolvedStage, *, policy=None, facts=None) -> dict:
    """Injected inputs are internal synthetic seams, not production measurements."""
    if not resolved.provider.is_local:
        return _assessment("not_applicable", "hosted", resolved)
    try:
        parsed, reason = _read_policy(settings) if policy is None else (
            LoadPolicy.model_validate(policy), "synthetic_policy")
    except (OSError, ValueError) as exc:
        return _assessment("error", "policy_configuration_error", resolved,
                           detail=f"{type(exc).__name__}: {exc}")
    if parsed is None:
        return _assessment("unknown", reason, resolved)
    if (parsed.envelope.placement not in {"cpu", "unified"}
            or parsed.envelope.memory_domain != "host_available_bytes"):
        return _assessment("unknown", "policy_envelope_unsupported", resolved)
    if parsed.identity != load_identity(resolved):
        return _assessment("unknown", "policy_identity_mismatch", resolved)
    try:
        sampled = fresh_load_facts() if facts is None else facts()
        available = sampled.get("available_bytes")
        if available is not None and (type(available) is not int or available < 0):
            raise ValueError("available_bytes must be a nonnegative integer")
        envelope = sampled.get("envelope")
        effective = LoadEnvelope.model_validate(envelope) if envelope is not None else None
    except Exception as exc:  # noqa: BLE001 — failed telemetry is not unavailable evidence
        return _assessment("error", "telemetry_failed", resolved,
                           detail=f"{type(exc).__name__}: {exc}")
    if effective is None:
        return _assessment("unknown", "effective_envelope_unknown", resolved)
    if effective != parsed.envelope:
        return _assessment("unknown", "policy_envelope_mismatch", resolved)
    if available is None:
        return _assessment("unknown", "telemetry_unavailable", resolved)
    admitted = available >= parsed.budget_bytes + parsed.reserve_bytes
    return _assessment("admitted" if admitted else "blocked",
                       "policy_satisfied" if admitted else "available_memory_insufficient", resolved,
                       policy_ref=parsed.policy_ref, envelope=effective.model_dump(),
                       available_bytes=available, budget_bytes=parsed.budget_bytes,
                       reserve_bytes=parsed.reserve_bytes)
