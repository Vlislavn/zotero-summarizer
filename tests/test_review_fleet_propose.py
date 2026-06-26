"""Unit tests for the review-fleet's pure truth-table ``propose_verdict``.

This is the fleet's glassbox brain: it folds the ALREADY-cached deep-review signals
(digest ``read_decision`` + A-D ``grade``, quality ``quality_band`` /
``overstatements`` / ``red_flags``, and the per-goal board) into ONE
``ProposedVerdict`` — with NO LLM call and NO I/O. So every case here is a plain,
deterministic mapping assertion; nothing is mocked because there is nothing to mock.

Coverage map:
  - the full read/skim/skip × grade × goal-match truth table (the four priorities);
  - the load-bearing ASYMMETRY: a goal-matched ``skip`` stays ``could_read`` (a wrong
    HIDE costs more than a wrong KEEP), only a goal-MISS skip may propose ``dont_read``;
  - the low-confidence WITHHOLD signals: ``quality_band == "uncertain"`` OR any
    non-empty ``overstatements`` cut confidence and raise a flag (the "double-check" hint);
  - the safe degrade-to-``could_read`` when no digest / no signals exist (never a hide).
"""
from __future__ import annotations

import pytest

from zotero_summarizer.models.triage import ProposedVerdict
from zotero_summarizer.services.library.review_fleet import propose


# --- helpers: build the cached-signal dicts the way the fleet reads them -----------


def _digest(read_decision=None, grade=None):
    d: dict = {}
    if read_decision is not None:
        d["read_decision"] = read_decision
    if grade is not None:
        d["grade"] = grade
    return d


def _quality(*, band="", overstatements=None, red_flags=None, grade=None):
    q: dict = {"quality_band": band}
    if overstatements is not None:
        q["overstatements"] = overstatements
    if red_flags is not None:
        q["red_flags"] = red_flags
    if grade is not None:
        q["grade"] = grade
    return q


def _goals(*, matched):
    """A per-goal board where ANY ``relevant`` cell means goal-matched."""
    return [{"relevant": True}, {"relevant": False}] if matched else [{"relevant": False}]


# --- 1. the read/skim/skip × grade × goal-match truth table ------------------------
#
# Each row is (read_decision, grade, goal_matched, expected_proposed). Derived
# straight from propose._base_verdict:
#   read  -> must (A/B) / should (C/D/?)
#   skim  -> should (A/B) / could (C/D/?)
#   skip  -> could (goal matched) / dont (goal miss)     <- the asymmetry
# Goal-match only changes the SKIP rows; for read/skim it is verdict-neutral, so we
# assert both goal values give the same verdict to lock that invariant in.

_TRUTH_TABLE = [
    # read: strong grade -> must_read, weak/none -> should_read (goal-neutral)
    ("read", "A", True, "must_read"),
    ("read", "A", False, "must_read"),
    ("read", "B", True, "must_read"),
    ("read", "C", True, "should_read"),
    ("read", "D", False, "should_read"),
    ("read", "", True, "should_read"),
    # skim: strong grade -> should_read, weak/none -> could_read (goal-neutral)
    ("skim", "A", True, "should_read"),
    ("skim", "B", False, "should_read"),
    ("skim", "C", True, "could_read"),
    ("skim", "D", False, "could_read"),
    ("skim", "", True, "could_read"),
    # skip: goal-matched -> could_read (kept), goal-miss -> dont_read (the only hide)
    ("skip", "A", True, "could_read"),
    ("skip", "C", True, "could_read"),
    ("skip", "", True, "could_read"),
    ("skip", "A", False, "dont_read"),
    ("skip", "C", False, "dont_read"),
    ("skip", "", False, "dont_read"),
]


@pytest.mark.parametrize("read_decision,grade,goal_matched,expected", _TRUTH_TABLE)
def test_truth_table_maps_to_expected_priority(read_decision, grade, goal_matched, expected):
    out = propose.propose_verdict(
        _digest(read_decision=read_decision, grade=grade or None),
        _quality(band="ok"),
        goal_summaries=_goals(matched=goal_matched),
    )
    assert isinstance(out, ProposedVerdict)
    assert out.proposed == expected
    # provenance echoes the inputs (for the UI/audit trail)
    assert out.digest_read_decision == read_decision
    assert out.grade == (grade or "")
    assert out.source == "review_fleet"
    assert 0.0 <= out.confidence <= 1.0


def test_read_and_skim_verdict_is_goal_independent():
    """The goal-match toggle must move ONLY the skip rows — read/skim are stable."""
    for decision in ("read", "skim"):
        for grade in ("A", "C", ""):
            matched = propose.propose_verdict(
                _digest(read_decision=decision, grade=grade or None),
                _quality(band="ok"),
                goal_summaries=_goals(matched=True),
            )
            missed = propose.propose_verdict(
                _digest(read_decision=decision, grade=grade or None),
                _quality(band="ok"),
                goal_summaries=_goals(matched=False),
            )
            assert matched.proposed == missed.proposed


# --- 2. the dont_read-conservatism (asymmetry) -------------------------------------


def test_goal_matched_skip_is_could_read_not_dont_read():
    """Load-bearing: a goal-matched skip must NOT propose a hide (could_read)."""
    out = propose.propose_verdict(
        _digest(read_decision="skip", grade="C"),
        _quality(band="ok"),
        goal_summaries=_goals(matched=True),
    )
    assert out.proposed == "could_read"
    assert out.proposed != "dont_read"


def test_goal_miss_skip_is_the_only_path_to_dont_read():
    out = propose.propose_verdict(
        _digest(read_decision="skip", grade="C"),
        _quality(band="ok"),
        goal_summaries=_goals(matched=False),
    )
    assert out.proposed == "dont_read"


@pytest.mark.parametrize("goal_summaries", [[{"relevant": False}], [{"relevant": False}, {}], [{}]])
def test_evaluated_goal_miss_allows_dont_read_on_skip(goal_summaries):
    """A REAL goal board (dict cells present, none ``relevant``) was evaluated and
    matched nothing — a true MISS, so a skip may propose the (only) hide."""
    out = propose.propose_verdict(
        _digest(read_decision="skip", grade="C"),
        _quality(band="ok"),
        goal_summaries=goal_summaries,
    )
    assert out.proposed == "dont_read"


@pytest.mark.parametrize("goal_summaries", [None, [], "not-a-list", ["junk", 3, None]])
def test_unknown_goal_board_keeps_could_read_on_skip(goal_summaries):
    """REGRESSION (no-wrong-hide): an ABSENT / empty / malformed goal board — e.g.
    the ``_paper_goal_summaries`` LLM call raised and ``deep_review`` swallowed it
    to ``None`` — is UNKNOWN, not a miss. A swallowed infra error must never flip a
    skip to a hide: the proposal stays ``could_read``, and its confidence drops
    below the card's 0.6 one-tap-Confirm floor so a human checks it (Override-only).
    """
    out = propose.propose_verdict(
        _digest(read_decision="skip", grade="C"),
        _quality(band="ok"),
        goal_summaries=goal_summaries,
    )
    assert out.proposed == "could_read"
    assert out.proposed != "dont_read"
    assert out.confidence < 0.6  # withheld → Override only


def test_no_digest_never_proposes_a_hide():
    """No digest read_decision at all -> a safe could_read, never dont_read,
    even on a goal MISS (no evidence is not evidence to hide)."""
    out = propose.propose_verdict(None, None, goal_summaries=_goals(matched=False))
    assert out.proposed == "could_read"
    assert out.digest_read_decision == ""


@pytest.mark.parametrize("bogus", ["maybe", "  ", "rejected", "yes", "reads"])
def test_unknown_read_decision_degrades_to_could_read(bogus):
    """Any read_decision outside {read,skim,skip} is normalized to '' (no digest)
    -> could_read; the provenance field reflects the normalized empty value.
    (Note: 'READ' is NOT bogus — it case-folds to a valid 'read'; covered below.)"""
    out = propose.propose_verdict(
        _digest(read_decision=bogus), None, goal_summaries=_goals(matched=False)
    )
    assert out.proposed == "could_read"
    assert out.digest_read_decision == ""


def test_read_decision_is_case_and_whitespace_normalized():
    out = propose.propose_verdict(
        _digest(read_decision="  Read ", grade="a"),
        _quality(band="ok"),
        goal_summaries=_goals(matched=False),
    )
    assert out.proposed == "must_read"  # 'Read'+'A' both normalized
    assert out.digest_read_decision == "read"
    assert out.grade == "A"


# --- 3. the low-confidence WITHHOLD cases (shaky quality signal) --------------------


def test_uncertain_band_lowers_confidence_and_flags():
    """quality_band == 'uncertain' is shaky: -0.2 confidence and a flag the UI
    foregrounds. Compared against the same call with a clean band."""
    clean = propose.propose_verdict(
        _digest(read_decision="read", grade="A"),
        _quality(band="ok"),
        goal_summaries=_goals(matched=True),
    )
    shaky = propose.propose_verdict(
        _digest(read_decision="read", grade="A"),
        _quality(band="uncertain"),
        goal_summaries=_goals(matched=True),
    )
    assert "quality_uncertain" in shaky.flags
    assert shaky.confidence == pytest.approx(round(clean.confidence - 0.2, 2))
    assert shaky.confidence < clean.confidence


def test_overstatements_lower_confidence_and_flag():
    """A non-empty overstatements list is shaky even with a confident band."""
    clean = propose.propose_verdict(
        _digest(read_decision="read", grade="A"),
        _quality(band="ok", overstatements=[]),
        goal_summaries=_goals(matched=True),
    )
    shaky = propose.propose_verdict(
        _digest(read_decision="read", grade="A"),
        _quality(band="ok", overstatements=["abstract claims X, body shows only Y"]),
        goal_summaries=_goals(matched=True),
    )
    assert "overstatements" in shaky.flags
    assert shaky.confidence == pytest.approx(round(clean.confidence - 0.2, 2))


def test_blank_overstatements_are_ignored():
    """Whitespace-only overstatement entries are stripped -> not shaky, no flag."""
    out = propose.propose_verdict(
        _digest(read_decision="read", grade="A"),
        _quality(band="ok", overstatements=["", "   ", "\t\n"]),
        goal_summaries=_goals(matched=True),
    )
    assert "overstatements" not in out.flags
    assert out.confidence == 0.85  # full read+A confidence, no cut


def test_shaky_rationale_carries_the_double_check_hint():
    out = propose.propose_verdict(
        _digest(read_decision="read", grade="A"),
        _quality(band="uncertain"),
        goal_summaries=_goals(matched=True),
    )
    assert "worth a check" in out.rationale


def test_flag_band_and_red_flags_surface_without_cutting_confidence():
    """``quality_band == 'flag'`` and ``red_flags`` raise flags for the UI but are
    NOT part of the 'shaky' confidence cut (only uncertain/overstatements are)."""
    flagged = propose.propose_verdict(
        _digest(read_decision="read", grade="A"),
        _quality(band="flag", red_flags=["n=3 underpowered"]),
        goal_summaries=_goals(matched=True),
    )
    clean = propose.propose_verdict(
        _digest(read_decision="read", grade="A"),
        _quality(band="ok"),
        goal_summaries=_goals(matched=True),
    )
    assert "quality_flag" in flagged.flags and "red_flags" in flagged.flags
    assert flagged.confidence == clean.confidence  # not a shaky cut


def test_missing_grade_with_a_decision_takes_a_small_confidence_penalty():
    """A read decision but no grade (-0.1), distinct from the shaky (-0.2) cut."""
    graded = propose.propose_verdict(
        _digest(read_decision="skim", grade="A"),
        _quality(band="ok"),
        goal_summaries=_goals(matched=False),
    )
    ungraded = propose.propose_verdict(
        _digest(read_decision="skim"),
        _quality(band="ok"),
        goal_summaries=_goals(matched=False),
    )
    assert ungraded.confidence < graded.confidence


def test_grade_falls_back_to_quality_eval_when_digest_lacks_it():
    """The grade is read from the digest first, then the quality_eval echo."""
    out = propose.propose_verdict(
        _digest(read_decision="read"),  # no grade in the digest
        _quality(band="ok", grade="B"),  # quality_eval carries it
        goal_summaries=_goals(matched=True),
    )
    assert out.grade == "B"
    assert out.proposed == "must_read"  # B is a high grade -> the read lift fires
