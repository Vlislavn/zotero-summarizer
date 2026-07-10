"""Phase 3: per-user calibration layer + Tier-1 env probe.

Precedence: code default < goals.yaml < data/calibration.json < ZS_* env.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from zotero_summarizer.services.config_overrides import (
    REGISTRY,
    apply_calibration,
    apply_env_overrides,
)
from zotero_summarizer.services.setup.bootstrap import _default_goals_config
from types import SimpleNamespace

from zotero_summarizer.services.setup.calibration import (
    decide_profile,
    digest_completeness,
    lean_tps_threshold,
    measure_throughput,
    read_calibration,
    tier1_env_calibrate,
    tier2_calibrate,
    tier3_recalibrate,
    upsert_calibration_entries,
)


def _fake_settings(tmp_path: Path, n_labels: int) -> SimpleNamespace:
    csv = tmp_path / "golden.csv"
    csv.write_text("priority\n" + "\n".join("could_read" for _ in range(n_labels)), encoding="utf-8")
    return SimpleNamespace(golden_csv_path=csv, calibration_path=tmp_path / "cal.json")


class _StubDigest:
    def __init__(self, **fields):
        self.__dict__.update(fields)


_FULL = dict(tldr="x", verdict="y", executive_summary="z", key_findings=["a"],
             methods="m", limitations="l", key_strength="s", key_weakness="w")


def _write_calibration(path: Path, entries: dict) -> None:
    path.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")


def test_apply_calibration_overrides_default(tmp_path: Path) -> None:
    cal = tmp_path / "calibration.json"
    _write_calibration(cal, {"quality_review.max_text_chars": {"value": 30000, "harness": "x"}})
    out = apply_calibration(_default_goals_config(), cal)
    assert out.quality_review.max_text_chars == 30000


def test_apply_calibration_absent_is_noop(tmp_path: Path) -> None:
    cal = tmp_path / "nope.json"
    cfg = _default_goals_config()
    assert apply_calibration(cfg, cal).model_dump() == cfg.model_dump()


def test_env_beats_calibration_beats_file(tmp_path: Path, monkeypatch) -> None:
    cal = tmp_path / "calibration.json"
    _write_calibration(cal, {"quality_review.max_text_chars": {"value": 30000}})
    cfg = _default_goals_config()  # code default 60000
    calibrated = apply_calibration(cfg, cal)
    assert calibrated.quality_review.max_text_chars == 30000  # calibration > default
    monkeypatch.setenv("ZS_QUALITY_REVIEW_MAX_TEXT_CHARS", "40000")
    final = apply_env_overrides(calibrated)
    assert final.quality_review.max_text_chars == 40000  # env > calibration


def test_decide_profile_threshold(monkeypatch) -> None:
    monkeypatch.delenv("ZS_CALIBRATION_LEAN_TPS", raising=False)
    assert decide_profile(5.0) == "lean"
    assert decide_profile(200.0) == "full"
    monkeypatch.setenv("ZS_CALIBRATION_LEAN_TPS", "100")
    assert lean_tps_threshold() == 100.0
    assert decide_profile(50.0) == "lean"


def test_tier1_lean_writes_caps_and_is_idempotent(tmp_path: Path) -> None:
    cal = tmp_path / "calibration.json"
    cfg = _default_goals_config()
    res = tier1_env_calibrate(cfg, cal, throughput=5.0)
    assert res["profile"] == "lean"
    payload = read_calibration(cal)
    assert payload["entries"]["quality_review.max_text_chars"]["value"] == cfg.quality_review.lean_max_text_chars
    assert payload["entries"]["quality_review.self_consistency_runs"]["value"] == cfg.quality_review.lean_self_consistency_runs
    assert payload["tier1"]["profile"] == "lean"
    # the lean caps actually take effect through the calibration layer
    assert apply_calibration(cfg, cal).quality_review.max_text_chars == cfg.quality_review.lean_max_text_chars
    # idempotent: a second probe (even with a 'full' reading) does not overwrite
    res2 = tier1_env_calibrate(cfg, cal, throughput=999.0)
    assert res2["status"] == "skipped"


def test_tier1_remote_is_always_full_without_probe(tmp_path: Path) -> None:
    cal = tmp_path / "calibration.json"
    res = tier1_env_calibrate(_default_goals_config(), cal, is_local=False)  # no llm, no throughput
    assert res["profile"] == "full"
    assert res["tokens_per_sec"] is None
    assert read_calibration(cal)["entries"] == {}
    assert read_calibration(cal)["tier1"]["reason"] == "remote endpoint (always full)"


def test_tier1_full_writes_no_overrides(tmp_path: Path) -> None:
    cal = tmp_path / "calibration.json"
    res = tier1_env_calibrate(_default_goals_config(), cal, throughput=200.0)
    assert res["profile"] == "full"
    assert read_calibration(cal)["entries"] == {}  # defaults stand


def test_measure_throughput_smoke() -> None:
    class _StubLLM:
        def prompt(self, _prompt):
            return "word " * 80
    assert measure_throughput(_StubLLM()) > 0


def test_upsert_merges_entries(tmp_path: Path) -> None:
    cal = tmp_path / "calibration.json"
    upsert_calibration_entries(cal, {"a.b": {"value": 1}}, meta_key="tier3", meta={"n": 1})
    upsert_calibration_entries(cal, {"c.d": {"value": 2}})
    payload = read_calibration(cal)
    assert set(payload["entries"]) == {"a.b", "c.d"}
    assert payload["tier3"] == {"n": 1}


def test_digest_completeness() -> None:
    assert digest_completeness(_StubDigest(**_FULL)) == 1.0
    empty = {k: ("" if isinstance(v, str) else []) for k, v in _FULL.items()}
    assert digest_completeness(_StubDigest(**empty)) == 0.0


def test_tier2_picks_lean_when_adequate_and_faster(tmp_path: Path) -> None:
    cfg = _default_goals_config()  # lean 12000, full 60000
    def run_digest(_t, _x, budget):
        secs = 1.0 if budget == cfg.quality_review.lean_max_text_chars else 3.0  # lean faster
        return _StubDigest(**_FULL), secs  # equal (full) completeness
    res = tier2_calibrate(cfg, tmp_path / "c.json", [("T", "body")], run_digest=run_digest)
    assert res["winner_max_text_chars"] == cfg.quality_review.lean_max_text_chars
    assert read_calibration(tmp_path / "c.json")["entries"]["quality_review.max_text_chars"]["value"] == 12000


def test_tier2_reports_progress(tmp_path: Path) -> None:
    """The sweep emits {completed, total} per digest so the UI can show 'reviewed N of M'
    (total = budgets × papers; the calibrate card + status poll rely on this)."""
    cfg = _default_goals_config()  # 2 budgets (lean + full)
    events: list[dict] = []
    tier2_calibrate(cfg, tmp_path / "c.json", [("T", "body")],
                    run_digest=lambda _t, _x, _b: (_StubDigest(**_FULL), 1.0),
                    progress=events.append)
    assert events[0] == {"phase": "reviewing", "completed": 0, "total": 2}
    assert events[-1]["completed"] == 2 and events[-1]["total"] == 2


def test_tier2_keeps_full_when_faster_at_equal_completeness(tmp_path: Path) -> None:
    # The remote case: equal completeness but the FULL budget is faster → keep full.
    cfg = _default_goals_config()
    def run_digest(_t, _x, budget):
        secs = 27.0 if budget == cfg.quality_review.lean_max_text_chars else 13.0  # full faster
        return _StubDigest(**_FULL), secs
    res = tier2_calibrate(cfg, tmp_path / "c.json", [("T", "b")], run_digest=run_digest)
    assert res["winner_max_text_chars"] == cfg.quality_review.max_text_chars
    assert read_calibration(tmp_path / "c.json")["entries"] == {}  # default kept


def test_tier2_keeps_full_budget_when_lean_worse(tmp_path: Path) -> None:
    cfg = _default_goals_config()
    def run_digest(_t, _x, budget):
        if budget == cfg.quality_review.lean_max_text_chars:
            return _StubDigest(tldr="x"), 1.0          # 1/8 substantive fields (even if fast)
        return _StubDigest(**_FULL), 2.0               # full coverage
    res = tier2_calibrate(cfg, tmp_path / "c.json", [("T", "b")], run_digest=run_digest)
    assert res["winner_max_text_chars"] == cfg.quality_review.max_text_chars
    assert read_calibration(tmp_path / "c.json")["entries"] == {}  # default kept, no override


def test_tier2_empty_papers_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        tier2_calibrate(_default_goals_config(), tmp_path / "c.json", [], run_digest=lambda *a: (None, 0.0))


def test_tier3_skips_below_min_labels(tmp_path: Path) -> None:
    s = _fake_settings(tmp_path, 10)
    res = tier3_recalibrate(s, run_eval=lambda _s: {"lightgbm": {"oof_spearman": 0.7}}, min_labels=200)
    assert res["status"] == "skipped"
    assert not s.calibration_path.exists()  # Tier-0 default stands; nothing written


def test_tier3_picks_best_classifier_on_user_labels(tmp_path: Path) -> None:
    s = _fake_settings(tmp_path, 300)
    res = tier3_recalibrate(
        s, min_labels=200,
        run_eval=lambda _s: {"lightgbm": {"oof_spearman": 0.65},
                             "tabpfn": {"oof_spearman": 0.80},
                             "logreg": {"oof_spearman": 0.57}},
    )
    assert res["winner_classifier"] == "tabpfn"
    payload = read_calibration(s.calibration_path)
    assert payload["entries"]["classifier_gate.model_name"]["value"] == "tabpfn"
    assert payload["tier3"]["winner_classifier"] == "tabpfn"
    assert payload["tier3"]["labels"] == 300


def test_user_owned_keys_never_in_env_registry_still_holds() -> None:
    # calibration + env share the system-owned surface; guard it didn't drift.
    from zotero_summarizer.models.config import USER_OWNED_KEYS
    for ov in REGISTRY:
        assert ov.path.split(".", 1)[0] not in USER_OWNED_KEYS
