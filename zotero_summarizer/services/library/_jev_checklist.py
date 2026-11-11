"""Jev-first checklist items (#146 design, #147): answer a few presence criteria with a Jev-compatible engine instead of the LLM rubric.

One request carries, per criterion, a Noul over the whole text and a Choice over tagged verbatim segments (``S000| …``); a
'yes' is accepted only when a second Noul on THAT segment alone confirms it (REQ-S5: never an unverified yes). P ≥ hi + a
confirmed segment → yes; P ≤ lo → no; everything else (and any error) goes to the LLM rubric. Bands are the held-out values
of the #146 report (agreement 0.95–0.98 on decided cases); the engine, P, band and segment are stored per item."""
from __future__ import annotations

import re
from typing import Any

from zotero_summarizer.integrations import jev
from zotero_summarizer.services.library._grounding import quote_is_grounded

_Q = {  # key -> (question, 'true' meaning, 'false' meaning)
    "code_data_released": ("give a link or repository (URL, DOI, accession) where the code, model weights or dataset of this work can be obtained",
                           "A concrete link or repository for this work's code or data is given",
                           "Nothing is released, or only 'available on request', or only third-party public datasets are cited"),
    "uncertainty": ("report uncertainty for its main results (confidence intervals, error bars, standard deviation over seeds, or a significance test)",
                    "Intervals, standard deviations over repeated runs, or p-values are reported for key numbers",
                    "Key results are single numbers with no spread or test"),
    "ablation": ("include an ablation or component analysis that removes or varies parts of the method to show what drives the result",
                 "Components or settings are removed or varied and the effect is reported", "Only the full method is evaluated"),
    "baselines": ("compare the proposed method against other published methods or strong baselines on the same evaluation",
                  "Results tables compare against named prior methods or baselines",
                  "There is no comparison with other methods, or only with the method's own variants"),
    "external_validation": ("evaluate on data from a different source than the training data (independent test set, other institution, other dataset, out-of-distribution)",
                            "Results are reported on a dataset or cohort different from the one used for training or tuning",
                            "All evaluation is on a split of the training data"),
}
BANDS = {"code_data_released": (0.10, 0.70), "uncertainty": (0.30, 0.90), "ablation": (0.10, 0.50),
         "baselines": (0.20, 0.70), "external_validation": (0.30, 0.95)}
SUPPORT_MIN = 0.70
_LINK = re.compile(r"https?://|doi\.org|10\.\d{4,}/|github\.com|gitlab\.com|zenodo|huggingface\.co|osf\.io|figshare", re.I)
_REF = re.compile(r"^\s*(references?|bibliography)\s*$", re.I | re.M)
MAX_SEGMENTS = 250


def segments(body: str, target: int = 700, lo: int = 150) -> list[str]:
    """Verbatim-ish segments (whitespace collapsed) of the body without its reference list."""
    refs = list(_REF.finditer(body))
    if refs and refs[-1].start() > 0.5 * len(body):
        body = body[:refs[-1].start()]
    out: list[str] = []
    for para in (p.strip() for p in re.split(r"\n\s*\n|\n(?=\[[A-Za-z])", body) if p.strip()):
        para = re.sub(r"\s*\n\s*", " ", para)
        if len(para) <= target * 1.4:
            out.append(para)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", para):
            if current and len(current) + len(sentence) > target:
                out.append(current)
                current = sentence
            else:
                current = (current + " " + sentence).strip()
        if current:
            out.append(current)
    merged: list[str] = []
    for piece in out:
        if merged and len(piece) < lo:
            merged[-1] += " " + piece
        else:
            merged.append(piece)
    return [s for s in merged if len(s) >= 40][:MAX_SEGMENTS]


def _noul(key: str, subject: str) -> dict:
    question, true, false = _Q[key]
    return {"type": "noul", "instructions": f"Does {subject} {question}?", "criteria": {"true": true, "false": false}}


def settle(target: jev.Target, keys: list[str], body: str, title: str) -> tuple[dict[str, str], dict[str, str], set[str], dict[str, Any]]:
    """(verdicts, evidence, grounded_yes, audit) for the items Jev settles; the rest are absent (→ LLM). Never raises."""
    audit: dict[str, Any] = {"engine": target.engine, "model": jev.MODEL, "status": "ok", "items": {}}
    wanted = [k for k in keys if k in BANDS]
    segs = segments(body)
    if not wanted or not segs:
        audit["status"] = "no_items"
        return {}, {}, set(), audit
    doc = "\n".join(f"S{i:03d}| {s}" for i, s in enumerate(segs))
    questions: dict[str, dict] = {}
    for key in wanted:
        questions[f"has_{key}"] = _noul(key, "`paper_text_excerpt`")
        questions[f"where_{key}"] = {"type": "choice", "instructions": f"Which segment of `paper_text_excerpt` best answers this? {_noul(key, '`paper_text_excerpt`')['instructions']}",
                                     "criteria": {f"S{i:03d}": None for i in range(len(segs))}}
    answers = jev.ask(target, {"paper_title": title or "", "paper_text_excerpt": doc}, questions)
    if answers is None:
        audit["status"] = "unavailable"
        return {}, {}, set(), audit
    verdicts: dict[str, str] = {}
    evidence: dict[str, str] = {}
    grounded: set[str] = set()
    candidates: dict[str, tuple[int, float]] = {}
    for key in wanted:
        try:
            p = round(float(answers[f"has_{key}"]["noul"]), 2)
            idx = int(str(answers[f"where_{key}"]["choice"])[1:])
        except (KeyError, TypeError, ValueError):
            audit["items"][key] = {"decided_by": "llm", "why": "unreadable answer"}
            continue
        lo, hi = BANDS[key]
        item = audit["items"][key] = {"p": p, "band": [lo, hi], "decided_by": "llm"}
        if p <= lo:
            verdicts[key], item["decided_by"] = "no", "jev"
        elif p >= hi and 0 <= idx < len(segs):
            candidates[key] = (idx, p)
    confirmed = _confirm(target, candidates, segs) if candidates else {}
    for key, (idx, _p) in candidates.items():
        support = confirmed.get(key)
        item = audit["items"][key]
        item["support_p"] = support
        ok = support is not None and support >= SUPPORT_MIN and (key != "code_data_released" or _LINK.search(segs[idx])) \
            and quote_is_grounded(segs[idx], body, fuzzy=True)
        if ok:
            verdicts[key], evidence[key], item["decided_by"], item["segment"] = "yes", segs[idx], "jev", f"S{idx:03d}"
            grounded.add(key)
        else:
            item["why"] = "yes not confirmed by a segment"
    return verdicts, evidence, grounded, audit


def _confirm(target: jev.Target, candidates: dict[str, tuple[int, float]], segs: list[str]) -> dict[str, float | None]:
    """A second Noul per candidate on its top segment ALONE."""
    state = {"segments": {k: segs[i] for k, (i, _p) in candidates.items()}}
    questions = {k: {**_noul(k, "this passage"), "instructions": f"Look ONLY at state.segments.{k}. Does this passage {_Q[k][0]}?"} for k in candidates}
    result = jev.noul(target, state, questions)
    return {k: (None if result is None else round(result[k], 2)) for k in candidates}
