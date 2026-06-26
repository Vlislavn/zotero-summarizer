"""Subsystem readiness — one signal, three surfaces.

A subsystem is either ready or not, with a human reason when not. The SAME
status feeds:

  1. a loud boot-time log line (``lifecycle.startup``),
  2. the additive ``subsystems`` field on ``GET /api/setup/status``, and
  3. :func:`require` — the action-boundary guard that raises a ``503`` so an
     action that MANDATORILY needs a dead subsystem fails fast with the real
     reason instead of degrading silently (the 2026-06-16 backlog-drain bug:
     ``lightgbm`` uninstalled → gate stayed ``None`` → the gate-only drain
     crashed per-item, swallowed unlogged, "nothing happened" in the UI).

Adding a subsystem = one checker function + one row in ``_CHECKERS``. Checks are
STATELESS on-demand probes (no registration registry, no init-order race),
mirroring :mod:`services.llm.operational_check`. A checker reports a not-ready
row; :func:`require` is the single place that RAISES (fail-fast). No broad
``except`` here — a checker reads state / probes an import and returns a row.
"""
from __future__ import annotations

import importlib.util

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.models.setup import SubsystemStatus
from zotero_summarizer.services._common import state


def check_dependency(module: str) -> SubsystemStatus:
    """Ready iff ``module`` is importable (``find_spec`` — resolves the spec, runs
    no module code). A missing critical dep is the difference between "the gate
    trains" and "the gate silently stays None"."""
    found = importlib.util.find_spec(module) is not None
    return SubsystemStatus(
        name=f"dep:{module}",
        ready=found,
        detail="" if found else (
            f"Python package '{module}' is not installed — run `uv sync` "
            "(declared in pyproject [project.dependencies])"
        ),
    )


def check_classifier_gate() -> SubsystemStatus:
    """Ready iff a live gate is installed. When it isn't, surface WHY —
    distinguishing 'still training' (wait) from 'retrain failed' (act) from a
    missing dependency (the usual root) so the reason is actionable."""
    st = state()
    if getattr(st, "classifier_gate", None) is not None:
        return SubsystemStatus(name="classifier_gate", ready=True)

    err = (getattr(st, "classifier_gate_error", "") or "").strip()
    if err:
        detail = f"gate retrain failed: {err}"
    elif getattr(st, "classifier_gate_training", False):
        detail = "gate is training in the background — retry shortly"
    else:
        # No gate, no recorded error: a missing dep is the usual cause, so name
        # it; otherwise it's a config/golden-CSV issue surfaced at startup.
        dep = check_dependency("lightgbm")
        detail = dep.detail if not dep.ready else (
            "classifier gate not loaded (check the golden CSV and startup logs)"
        )
    return SubsystemStatus(name="classifier_gate", ready=False, detail=detail)


# name -> checker. Order = boot-log + /status display order. New subsystem ⇒ add
# a checker above and one row here; nothing else to wire.
_CHECKERS = {
    "lightgbm": lambda: check_dependency("lightgbm"),
    "classifier_gate": check_classifier_gate,
}


def all_statuses() -> list[SubsystemStatus]:
    """Every registered subsystem's current readiness (on-demand probe)."""
    return [checker() for checker in _CHECKERS.values()]


def require(name: str) -> None:
    """Action-boundary fail-fast: raise ``503`` if subsystem ``name`` isn't ready.

    The generalizable guard — any route/action that MANDATORILY needs a
    subsystem calls this at its boundary, turning a silently-degraded run into an
    immediate, actionable error carrying the real reason.
    """
    checker = _CHECKERS.get(name)
    if checker is None:
        raise APIError(
            error="unknown_subsystem",
            message=f"no readiness checker registered for {name!r}",
            status_code=500,
        )
    status = checker()
    if not status.ready:
        raise APIError(
            error=f"{name}_unavailable",
            message=status.detail,
            status_code=503,
            details={"subsystem": name},
        )
