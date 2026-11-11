"""Which two comments of a thread disagree (Todo #184) — detected, never generated.

Candidate pairs come from the reply structure: a comment and the one it answers (when either was picked for the gist) plus the
picked comments among themselves. ONE ``noul`` request per thread asks Jev two plain questions per pair; a pair counts when the
SMALLER of its two answers reaches ``TAU`` and the best such pair (at most one per thread) is returned. Measured on 371 hand-labelled
pairs of 30 threads (``data/perf-20261107/telegram/faith/dispute``): precision 19/19 (Wilson 0.83–1.00) at TAU 0.7, held-out 11/11 (0.74–1.00),
a block on 13 of 30 threads. ``noul`` unreachable → no pair: nothing local (mDeBERTa AUC 0.56, cue words 0.65, 4B yes/no 0.52) is reliable enough."""
from __future__ import annotations

TAU = 0.70       # meets the ship bar: held-out 11/11 (Wilson 0.74-1.00), all pairs 19/19; production repeats 10/10 and 11/12 (answers move ±0.05 between runs)
MAX_PAIRS = 12   # candidates per thread (reply pairs first); one request carries 2 questions each
TEXT_CHARS = 1500
_ASK = ("Look ONLY at state.pairs.{k}. {q} (`A` and `B` are two chat comments; B may be a reply to A.)")
_QUESTIONS = ("Комментарий B возражает комментарию A?", "Комментарий B утверждает противоположное тому, что утверждает комментарий A?")
_CRITERIA = {"true": "B says A is wrong, rejects A's claim, or claims the opposite of it",
             "false": "B agrees with A, adds to it, answers a question, changes the subject, or says something unrelated"}


def pairs(thread, picked: list[str]) -> list[tuple[str, str]]:
    """(A, B) short ids: B answers A, or both were picked. Reply pairs first, message order, at most ``MAX_PAIRS``."""
    chosen = [(thread.parent[x], x) for x in thread.comment if thread.parent.get(x) in thread.comment
              and (x in picked or thread.parent[x] in picked)]
    chosen += [(a, b) for i, a in enumerate(picked) for b in picked[i + 1:] if (a, b) not in chosen and (b, a) not in chosen]
    return chosen[:MAX_PAIRS]


def find(thread, picked: list[str], noul) -> tuple[str, str] | None:
    """The most clearly disagreeing (A, B) pair, or None. ``noul(state, questions) -> {id: P(yes)} | None``."""
    candidates = pairs(thread, picked)
    if not candidates or noul is None:
        return None
    plain = lambda label: " ".join(thread.comment[label].text.split())[:TEXT_CHARS]  # noqa: E731
    state = {"pairs": {f"p{i}": {"A": plain(a), "B": plain(b)} for i, (a, b) in enumerate(candidates)}}
    questions = {f"p{i}_{j}": {"type": "noul", "instructions": _ASK.format(k=f"p{i}", q=q), "criteria": _CRITERIA}
                 for i in range(len(candidates)) for j, q in enumerate(_QUESTIONS)}
    answers = noul(state, questions)
    if answers is None:
        return None
    best = max(range(len(candidates)), key=lambda i: min(answers[f"p{i}_0"], answers[f"p{i}_1"]))
    return candidates[best] if min(answers[f"p{best}_0"], answers[f"p{best}_1"]) >= TAU else None


__all__ = ["find", "pairs", "TAU"]
