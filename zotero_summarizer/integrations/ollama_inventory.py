"""Read-only Ollama inventory (``/api/tags``, ``/api/ps``) plus a best-effort unload.

Metadata only: nothing here loads a model or sends a prompt. ``unload`` asks the
daemon to drop a model (``keep_alive: 0``, no prompt), never to load one. Every
function returns ``None``/``False`` on any failure — a non-Ollama endpoint, a
timeout or a malformed answer means "unknown", never a guess.
"""
from __future__ import annotations

import httpx

_TIMEOUT_SECS = 5.0


def ollama_root(base_url: str | None) -> str | None:
    """``http://host:11434/v1`` -> ``http://host:11434``; non-HTTP bases -> None."""
    root = (base_url or "").strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root if root.startswith(("http://", "https://")) else None


def _names(entry: dict) -> set[str]:
    return {str(entry.get(key)) for key in ("name", "model") if entry.get(key)}


def _wanted(model: str) -> set[str]:
    return {model, model + ":latest"} if ":" not in model else {model}


def _models(root: str, path: str) -> list[dict] | None:
    try:
        response = httpx.get(root + path, timeout=_TIMEOUT_SECS, trust_env=False, follow_redirects=False)
        response.raise_for_status()
        models = response.json().get("models")
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else None


def model_size_bytes(root: str, model: str) -> int | None:
    """Ollama's own size for ``model`` (``/api/tags``), or None when unknown."""
    for entry in _models(root, "/api/tags") or []:
        size = entry.get("size")
        if _names(entry) & _wanted(model) and type(size) is int and size > 0:
            return size
    return None


def is_resident(root: str, model: str) -> bool:
    """True only when ``/api/ps`` lists the model as currently loaded."""
    return any(_names(entry) & _wanted(model) for entry in _models(root, "/api/ps") or [])


def unload(root: str, model: str) -> bool:
    """Drop ``model`` now (empty request, ``keep_alive: 0``) only if ``/api/ps`` lists it; best effort.

    The residency check comes first because an empty generate request would otherwise
    *load* a model that is not resident.
    """
    if not is_resident(root, model):
        return False
    try:
        httpx.post(root + "/api/generate", json={"model": model, "keep_alive": 0},
                   timeout=_TIMEOUT_SECS, trust_env=False, follow_redirects=False).raise_for_status()
    except httpx.HTTPError:
        return False
    return True
