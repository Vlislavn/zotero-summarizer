"""Acquire-before-score rescue for abstract-less, high-goal feed items.

Regression for the real miss: "Towards Conversational AI for Disease Management"
(Nature, DOI 10.1038/s41586-026-10764-5) arrived through the Nature RSS feed with
only a boilerplate publication-notice "abstract", so the classifier gate scored it
on no content and dropped it to ``dont_read`` — despite a 0.556 research-goal cosine.
The rescue recovers its full text and re-scores it before the verdict stands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from zotero_summarizer.services.triage.feeds import _tick_phases
from zotero_summarizer.services.triage.feeds._common import _has_usable_abstract

# The exact boilerplate the Nature feed delivered (processed_feed_items id 11972).
_NATURE_TITLE = "Towards Conversational AI for Disease Management"
_NATURE_BOILERPLATE = (
    '<p xmlns="http://www.w3.org/1999/xhtml">Nature, Published online: 17 June 2026; '
    '<a href="https://www.nature.com/articles/s41586-026-10764-5">'
    "doi:10.1038/s41586-026-10764-5</a></p>" + _NATURE_TITLE
)
_REAL_ABSTRACT = (
    "We present a conversational agent for longitudinal disease management that "
    "couples a clinical knowledge base with multi-turn dialogue. Across a cohort "
    "of oncology patients the system improved adherence and triage accuracy "
    "versus standard of care, with calibrated uncertainty and abstention. " * 2
)


# ---------------------------------------------------------------------------
# _has_usable_abstract — the predicate that flags rescue candidates
# ---------------------------------------------------------------------------


def test_nature_boilerplate_is_not_a_usable_abstract():
    item = {"title": _NATURE_TITLE, "abstract": _NATURE_BOILERPLATE}
    assert _has_usable_abstract(item) is False


def test_real_abstract_is_usable():
    item = {"title": _NATURE_TITLE, "abstract": _REAL_ABSTRACT}
    assert _has_usable_abstract(item) is True


def test_empty_abstract_is_not_usable():
    assert _has_usable_abstract({"title": "x", "abstract": ""}) is False


# ---------------------------------------------------------------------------
# recover_abstractless_rescues — the rescue partition
# ---------------------------------------------------------------------------


@dataclass
class _StubPred:
    """Mimics FeedPrediction for the one field the rescue reads."""

    aux_context: dict = field(default_factory=dict)


def _pred(max_goal: float) -> _StubPred:
    return _StubPred(aux_context={"goal_sims": {"clinics": max_goal, "other": 0.1}})


def _cfg(**over):
    base = dict(enabled=True, goal_sim_threshold=0.45, max_per_tick=3, min_abstract_chars=120)
    base.update(over)
    return SimpleNamespace(**base)


def _patch_state(monkeypatch, cfg):
    state = SimpleNamespace(app_state=SimpleNamespace(config=SimpleNamespace(recover_abstract=cfg)))
    monkeypatch.setattr(_tick_phases, "get_state", lambda: state)


def _patch_fetch_and_score(monkeypatch, *, path, score=3.8):
    """Patch the I/O boundaries: full-text fetch + LLM re-score + prestige."""
    monkeypatch.setattr(
        "zotero_summarizer.services.library._pdf_acquire.acquire_pdf_for",
        lambda key, detail: SimpleNamespace(path=path, needs_login=False),
    )
    monkeypatch.setattr(
        "zotero_summarizer.services.triage.summarization.run_pipeline",
        lambda req, log_prefix=None: SimpleNamespace(
            composite_relevance_score=score, reading_priority="should_read",
        ),
    )
    monkeypatch.setattr(
        "zotero_summarizer.services.triage.feeds._triage._apply_prestige",
        lambda summary, item, *, log_prefix: None,
    )


def _item(key, abstract):
    return {"item_key": key, "item_id": int(key[1:]), "title": _NATURE_TITLE,
            "abstract": abstract, "doi": "10.1038/s41586-026-10764-5", "url": ""}


def test_rescues_abstractless_high_goal_item(monkeypatch):
    """The Nature paper: no usable abstract + goal_sim 0.556 → fetched + re-scored."""
    _patch_state(monkeypatch, _cfg())
    _patch_fetch_and_score(monkeypatch, path=Path("/tmp/paper.pdf"), score=3.8)

    boiler = _item("K1", _NATURE_BOILERPLATE)
    real = _item("K2", _REAL_ABSTRACT)         # has abstract → gate verdict stands
    lowgoal = _item("K3", _NATURE_BOILERPLATE)  # abstract-less but goal too low
    gate_rejected = [(boiler, _pred(0.556)), (real, _pred(0.9)), (lowgoal, _pred(0.1))]

    rescued, still_rejected = _tick_phases.recover_abstractless_rescues(
        gate_rejected, tick_id="tick",
    )

    assert [it["item_key"] for it, _ in rescued] == ["K1"]
    assert rescued[0][1].composite_score == 3.8
    assert {it["item_key"] for it, _ in still_rejected} == {"K2", "K3"}


def test_no_fetchable_full_text_keeps_gate_verdict(monkeypatch):
    """Acquisition yields nothing → the item stays gate-rejected (verdict stands)."""
    _patch_state(monkeypatch, _cfg())
    _patch_fetch_and_score(monkeypatch, path=None)

    boiler = _item("K1", _NATURE_BOILERPLATE)
    rescued, still_rejected = _tick_phases.recover_abstractless_rescues(
        [(boiler, _pred(0.556))], tick_id="tick",
    )
    assert rescued == []
    assert [it["item_key"] for it, _ in still_rejected] == ["K1"]


def test_max_per_tick_caps_browser_fetches(monkeypatch):
    """Two eligible items but cap=1 → one rescued, one deferred to still_rejected."""
    _patch_state(monkeypatch, _cfg(max_per_tick=1))
    _patch_fetch_and_score(monkeypatch, path=Path("/tmp/paper.pdf"))

    a = _item("K1", _NATURE_BOILERPLATE)
    b = _item("K2", _NATURE_BOILERPLATE)
    rescued, still_rejected = _tick_phases.recover_abstractless_rescues(
        [(a, _pred(0.7)), (b, _pred(0.7))], tick_id="tick",
    )
    assert len(rescued) == 1
    assert len(still_rejected) == 1


def test_disabled_returns_everything_unchanged(monkeypatch):
    _patch_state(monkeypatch, _cfg(enabled=False))
    gate_rejected = [(_item("K1", _NATURE_BOILERPLATE), _pred(0.9))]
    rescued, still_rejected = _tick_phases.recover_abstractless_rescues(
        gate_rejected, tick_id="tick",
    )
    assert rescued == []
    assert still_rejected == gate_rejected


def test_unscorable_pred_none_is_never_rescued(monkeypatch):
    """A gate item with no prediction (pred=None) can't be re-scored — stays rejected."""
    _patch_state(monkeypatch, _cfg())
    gate_rejected = [(_item("K1", _NATURE_BOILERPLATE), None)]
    rescued, still_rejected = _tick_phases.recover_abstractless_rescues(
        gate_rejected, tick_id="tick",
    )
    assert rescued == []
    assert still_rejected == gate_rejected
