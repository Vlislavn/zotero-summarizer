"""Jev (TypeSafe System One) wire client — HTTP only; which engine and key to use is decided by ``services/llm/jev_engine.py``.

``ask`` posts typed questions (Noul / Choice) over a state to ``target``; any failure returns ``None`` — never a 'no'.
The same wire format is spoken by the Cloud service and by any local Jev-compatible server (REQ-S35)."""
from __future__ import annotations

import logging
from typing import NamedTuple

import httpx

log = logging.getLogger(__name__)
CLOUD_URL = "https://api.typesafe.ai/v1/systemone"
LOCAL_URL = "http://127.0.0.1:8766/v1/systemone"
MODEL = "jev-1.14.0"  # pinned: thresholds are tuned against one version, not the moving alias


class Target(NamedTuple):
    engine: str  # "cloud" | "local"
    url: str
    key: str = ""  # sent as a bearer token when non-empty; never logged


def ask(target: Target, state: dict, questions: dict[str, dict], *, timeout: float = 60.0) -> dict[str, dict] | None:
    """{question id: raw answer} for ``questions`` over ``state`` in ONE request; None when the engine is unreachable or refuses."""
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {target.key}"} if target.key else {})}
    try:
        response = httpx.post(target.url, json={"model": MODEL, "state": state, "questions": questions}, headers=headers, timeout=timeout)
        response.raise_for_status()
        answers = response.json()["answers"]
        return {name: answers[name] for name in questions}
    except Exception as error:  # network, HTTP status, shape: the caller degrades, the text never leaks into the log
        log.warning("jev unavailable: %s", type(error).__name__)
        return None


def noul(target: Target, state: dict, questions: dict[str, dict], *, timeout: float = 60.0) -> dict[str, float] | None:
    """{question id: P(yes)} for Noul ``questions``; None on any failure."""
    answers = ask(target, state, questions, timeout=timeout)
    try:
        return None if answers is None else {name: float(answers[name]["noul"]) for name in questions}
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ["ask", "noul", "Target", "CLOUD_URL", "LOCAL_URL"]
