"""active_learning._ground_truth — disagreement is judged against the user's
EFFECTIVE (label:*-aware) priority, not the noisy derived label. Pure helper,
so no trained model is needed."""

from __future__ import annotations

from zotero_summarizer.services.model.active_learning import _ground_truth


_ROW = {"item_key": "ABCD1234", "gold_priority_final": "could_read"}


def test_no_hybrid_map_falls_back_to_derived():
    current, has_label = _ground_truth("ABCD1234", _ROW, None)
    assert (current, has_label) == ("could_read", False)


def test_explicit_user_label_wins_and_flags_has_label():
    effective = {"ABCD1234": {"effective_priority": "must_read", "source": "user"}}
    current, has_label = _ground_truth("ABCD1234", _ROW, effective)
    assert (current, has_label) == ("must_read", True)


def test_derived_source_uses_effective_without_label_flag():
    effective = {"ABCD1234": {"effective_priority": "could_read", "source": "derived"}}
    current, has_label = _ground_truth("ABCD1234", _ROW, effective)
    assert (current, has_label) == ("could_read", False)


def test_key_absent_from_hybrid_map_falls_back_to_derived():
    current, has_label = _ground_truth("ABCD1234", _ROW, {"OTHERKEY": {}})
    assert (current, has_label) == ("could_read", False)


def test_unknown_when_no_signal_anywhere():
    current, has_label = _ground_truth("ZZZZ9999", {"item_key": "ZZZZ9999"}, None)
    assert (current, has_label) == ("unknown", False)
