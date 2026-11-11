"""Deterministic "what is published" + method checklist.

A ported, trimmed subset of the card ``det_v3`` checks (``data/perf-20261107/card``):
pure regex over the paper's full text with the reference list stripped (appendices kept) — no LLM,
no network. Each chip is ``{"present": bool, "evidence": quote|None}``.

"What is published" answers the owner's "methods you can take" rule: prompts,
evaluation protocol, judge-vs-human validation *with the reported number*, agent
contracts, released data. The method checklist flags the reusable-method signals
(baselines, ablation, uncertainty, external validation, reproducibility detail).
"""
from __future__ import annotations

import re
from typing import Any

from zotero_summarizer.services.library._paper_body import _strip_references


def _body(text: str) -> str:
    """Strip the reference list (appendices after it stay) and join line wraps (keeps quotes readable)."""
    t, _removed = _strip_references(text or "")
    return re.sub(r"[ \t]*\n[ \t]*", " ", t)


def _quote(t: str, m: re.Match, width: int = 200) -> str:
    a, b = max(0, m.start() - width // 2), min(len(t), m.end() + width // 2)
    return re.sub(r"\s+", " ", t[a:b]).strip()


def _evidence(rx: re.Pattern, t: str) -> str | None:
    m = rx.search(t)
    return _quote(t, m) if m else None


_PROMPTS = re.compile(
    r"\b(system|user|full|exact|our)\s+prompts?\b"
    r"|\bprompt(s)? (are|is|were|used)\b[^.]{0,40}(appendix|listed|provided|shown|given|table|figure)"
    r"|\bprompt templates?\b|\bwe (provide|include|release|list|show) (the )?prompts?\b", re.I)
_EVAL_PROTOCOL = re.compile(
    r"\b(evaluation|scoring|grading|annotation) (protocol|rubric|procedure|guidelines?|criteria|setup)\b"
    r"|\brubrics?\b|\bprogrammatically verifiable\b|\bexpert rubrics?\b|\bverifiable (outcome|reward)\b", re.I)
_AGENT_CONTRACT = re.compile(
    r"\b(tool|action|api) (contract|schema|interface|boundary)\b"
    r"|\b(typed |finite-?state )?(session|protocol) (type|enforce|state)\w*"
    r"|\bcapability (token|based)\b|\bstate machine\b|\bMCP\b"
    r"|\bpolicy (engine|language|enforce|constraint)\b|\bruntime authoriz\w+", re.I)
_DATA_RELEASED = re.compile(
    r"\b(dataset|benchmark|data)\s+(is|are|will be)?\s*(publicly )?(available|released)\b"
    r"|\bwe (release|provide|publish) (the )?(dataset|data|benchmark)\b"
    r"|\b\d{2,}[,\d]* (tasks|problems|instances|examples|cases)\b", re.I)
# Judge validated against humans, AND the reported agreement/correlation number.
_JUDGE = re.compile(
    r"(cohen|krippendorff|fleiss|\u03ba|kappa|inter-?annotator|inter-?rater"
    r"|agreement with (human|expert)|human agreement|correlation with (human|expert)"
    r"|validat\w+ (against|with) (human|expert)|judge\w* (agree|correlat|valid))", re.I)
# A kappa/correlation value (0.00–1.00) sits right after the cue — preferred over a
# stray percentage from an unrelated sentence.
_AGREE_DECIMAL = re.compile(
    r"(cohen|krippendorff|fleiss|\u03ba|kappa|agreement|correlation|inter-?annotator|inter-?rater)"
    r"[^.\n]{0,40}?(0\.\d{2,3})", re.I)
_NUMBER = re.compile(r"\b\d?\.\d{2,3}\b|\b\d{1,3}(?:\.\d+)?\s*%")


def published_facts(text: str) -> dict[str, Any]:
    t = _body(text)
    out: dict[str, Any] = {}
    for name, rx in (
        ("prompts", _PROMPTS), ("evaluation_protocol", _EVAL_PROTOCOL),
        ("agent_contracts", _AGENT_CONTRACT), ("data_released", _DATA_RELEASED),
    ):
        ev = _evidence(rx, t)
        out[name] = {"present": ev is not None, "evidence": ev}

    judge: dict[str, Any] = {"present": bool(_JUDGE.search(t)), "numbers": [], "evidence": None}
    decimal = _AGREE_DECIMAL.search(t)  # prefer an explicit kappa/correlation value
    if decimal:
        judge = {"present": True, "numbers": [decimal.group(2)], "evidence": _quote(t, decimal)}
    else:
        for m in _JUDGE.finditer(t):
            window = t[max(0, m.start() - 20): m.end() + 60]
            numbers = [n.group(0) for n in _NUMBER.finditer(window)]
            if numbers:
                judge = {"present": True, "numbers": numbers[:3], "evidence": _quote(t, m)}
                break
    out["judge_validation"] = judge
    return out


_BASELINES = re.compile(
    r"\bbaselines?\b|\bcompared? (with|to|against)\b|\boutperform|\bstate[- ]of[- ]the[- ]art\b|\bSOTA\b", re.I)
_ABLATION = re.compile(
    r"\bablat|\bw/o\b|\bwithout (the )?(\w+ ){0,2}(module|component|loss|objective)\b|\bcomponent analysis\b", re.I)
_UNCERTAINTY = re.compile(
    r"\d\s*(\u00b1|\+/-)\s*\d|\b95\s*%\s*(CI|confidence)|\berror bars?\b"
    r"|\b(across|over)\s+\w+\s+(seeds|runs|trials)\b|\bt-?test\b|\bp\s*[<=\u2264]\s*0?\.\d|\bbootstrap", re.I)
_EXTERNAL = re.compile(
    r"\bexternal (validation|test|cohort|dataset)\b|\bheld[- ]out (set|site|cohort|benchmark|domain)\b"
    r"|\bout[- ]of[- ]distribution\b|\bcross[- ](site|domain|dataset|cohort)\b"
    r"|\bgenerali[sz]\w+ (to|across) (unseen|new|other)\b", re.I)
_REPRO = re.compile(
    r"\blearning rate\b|\bbatch size\b|\bhyper-?parameters?\b|\bepochs?\b|\btemperature\s*(=|of|set)"
    r"|\bimplementation details\b|\bexperimental (setup|settings)\b"
    r"|\bprompts? (are|is) (given|provided|listed) in", re.I)


def method_checklist(text: str) -> dict[str, dict]:
    t = _body(text)
    checks = (
        ("baselines", _BASELINES), ("ablation", _ABLATION), ("uncertainty", _UNCERTAINTY),
        ("external_validation", _EXTERNAL), ("reproducibility", _REPRO),
    )
    out: dict[str, dict] = {}
    for key, rx in checks:
        ev = _evidence(rx, t)
        out[key] = {"present": ev is not None, "evidence": ev}
    return out


__all__ = ["published_facts", "method_checklist"]
