"""Subsystem readiness primitive — the fail-fast guard behind the 2026-06-16 fix.

A missing dep / unavailable gate must surface as a not-ready status and make
``require`` raise a 503, instead of leaving the gate a silent None that crashes
the gate-only backlog drain per-item.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from zotero_summarizer.api.errors import APIError
from zotero_summarizer.services import readiness


def _gate_state(*, gate=None, error="", training=False):
    return SimpleNamespace(
        classifier_gate=gate,
        classifier_gate_error=error,
        classifier_gate_training=training,
    )


# --- check_dependency ------------------------------------------------------


def test_check_dependency_ready_for_stdlib():
    st = readiness.check_dependency("json")
    assert st.ready is True
    assert st.detail == ""


def test_check_dependency_not_ready_for_missing():
    st = readiness.check_dependency("nope_missing_xyz")
    assert st.ready is False
    assert "not installed" in st.detail


# --- check_classifier_gate -------------------------------------------------


def test_gate_ready_when_live(monkeypatch):
    monkeypatch.setattr(readiness, "state", lambda: _gate_state(gate=object()))
    assert readiness.check_classifier_gate().ready is True


def test_gate_not_ready_when_none(monkeypatch):
    monkeypatch.setattr(readiness, "state", lambda: _gate_state(gate=None))
    st = readiness.check_classifier_gate()
    assert st.ready is False
    assert st.detail  # carries a human reason


def test_gate_surfaces_retrain_error(monkeypatch):
    monkeypatch.setattr(
        readiness, "state",
        lambda: _gate_state(gate=None, error="ModuleNotFoundError: No module named 'lightgbm'"),
    )
    st = readiness.check_classifier_gate()
    assert st.ready is False
    assert "lightgbm" in st.detail


def test_gate_distinguishes_training(monkeypatch):
    monkeypatch.setattr(readiness, "state", lambda: _gate_state(gate=None, training=True))
    st = readiness.check_classifier_gate()
    assert st.ready is False
    assert "training" in st.detail.lower()


# --- require (the action-boundary fail-fast) -------------------------------


def test_require_raises_503_when_not_ready(monkeypatch):
    monkeypatch.setattr(readiness, "state", lambda: _gate_state(gate=None))
    with pytest.raises(APIError) as ei:
        readiness.require("classifier_gate")
    assert ei.value.status_code == 503
    assert ei.value.error == "classifier_gate_unavailable"
    assert ei.value.details.get("subsystem") == "classifier_gate"


def test_require_passes_when_ready(monkeypatch):
    monkeypatch.setattr(readiness, "state", lambda: _gate_state(gate=object()))
    readiness.require("classifier_gate")  # must not raise


def test_require_unknown_subsystem_is_500():
    with pytest.raises(APIError) as ei:
        readiness.require("does_not_exist")
    assert ei.value.status_code == 500


# --- all_statuses ----------------------------------------------------------


def test_all_statuses_covers_registered_subsystems(monkeypatch):
    monkeypatch.setattr(readiness, "state", lambda: _gate_state(gate=object()))
    names = {s.name for s in readiness.all_statuses()}
    assert "dep:lightgbm" in names
    assert "classifier_gate" in names
