"""Fresh memory reading admits one short local-model Test when no approval policy is set.

Ollama's own size for the model is the footprint; a model merely being resident is
not evidence of safety, so the RAM/swap floors always apply. Every refusal happens
before any client or credential exists. This is a point-in-time reading, not a
hardware-safety certificate: ``hardware_safe`` stays ``unknown``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import psutil

from zotero_summarizer.integrations import ollama_inventory
from zotero_summarizer.models.providers import ProviderConfig

GIB = 1024 ** 3
RAM_FACTOR = 1.25          # available RAM must cover footprint * 1.25 ...
RAM_FLOOR = 4 * GIB        # ... plus this headroom
SWAP_FREE_FLOOR = 2 * GIB  # free swap must cover max(this, 15% of total swap)
SWAP_FREE_FRACTION = 0.15
KV_BYTES_PER_TOKEN = 256 * 1024  # conservative KV-cache allowance per context token (provider.num_ctx)
PROBE_MAX_TOKENS = 16
_BLOCKED = {"memory_insufficient", "swap_nearly_full"}
REFUSALS = _BLOCKED | {"model_size_unknown", "memory_telemetry_unavailable"}


def read_memory() -> dict:
    """Fresh available RAM and swap (bytes); the single seam tests may replace."""
    swap = psutil.swap_memory()
    return {"ram_available": int(psutil.virtual_memory().available),
            "swap_used": int(swap.used), "swap_total": int(swap.total)}


def _gib(value: int) -> str:
    return f"{value / GIB:.1f}"


def _decide(footprint: int | None, resident: bool, reading: dict | None, kv_allowance: int = 0) -> tuple[str, str, dict]:
    evidence = {"ram_available": None, "swap_used": None, "swap_total": None, "footprint": footprint,
                "resident": resident, "thresholds": None,
                "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if footprint is None and not resident:
        return "model_size_unknown", "Ollama did not report this model's size", evidence
    if reading is None:
        return "memory_telemetry_unavailable", "the memory reading failed", evidence
    evidence.update(reading)
    ram_needed = (0 if resident else int(footprint * RAM_FACTOR)) + kv_allowance + RAM_FLOOR
    swap_total = reading["swap_total"]
    swap_free_needed = max(SWAP_FREE_FLOOR, int(swap_total * SWAP_FREE_FRACTION)) if swap_total else 0
    evidence["thresholds"] = {"ram_needed": ram_needed, "swap_free_needed": swap_free_needed,
                                "kv_allowance": kv_allowance}
    swap_note = f"swap used {_gib(reading['swap_used'])} of {_gib(swap_total)} GiB"
    if reading["ram_available"] < ram_needed:
        return ("memory_insufficient", f"free {_gib(reading['ram_available'])} GiB, need ≥ {_gib(ram_needed)} GiB; "
                f"{swap_note}", evidence)
    if swap_total - reading["swap_used"] < swap_free_needed:
        return ("swap_nearly_full", f"{swap_note}, need ≥ {_gib(swap_free_needed)} GiB swap free; "
                f"free RAM {_gib(reading['ram_available'])} GiB", evidence)
    return "admitted", f"free {_gib(reading['ram_available'])} GiB, {swap_note}", evidence


def assess(provider: ProviderConfig, model: str) -> dict:
    """``{status, reason_code, message, evidence}`` from a fresh reading; never loads anything."""
    root = ollama_inventory.ollama_root(provider.base_url)
    footprint = ollama_inventory.model_size_bytes(root, model) if root else None
    resident = bool(root) and ollama_inventory.is_resident(root, model)
    try:
        reading = read_memory()
    except Exception:  # noqa: BLE001 — a failed reading refuses; it is never guessed
        reading = None
    code, message, evidence = _decide(footprint, resident, reading, (provider.num_ctx or 0) * KV_BYTES_PER_TOKEN)
    status = "admitted" if code == "admitted" else "blocked" if code in _BLOCKED else "unknown"
    return {"status": status,
            "reason_code": "memory_checked" if code == "admitted" else code,
            "message": message, "evidence": evidence}


def release_after_probe(provider: ProviderConfig, model: str, admission: dict) -> None:
    """Unload a model this Test loaded: not resident at admission, resident now (``unload`` re-checks)."""
    root = ollama_inventory.ollama_root(provider.base_url)
    if root and (admission.get("evidence") or {}).get("resident") is False:
        ollama_inventory.unload(root, model)
