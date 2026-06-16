"""At-a-glance decision aid for the paper brief — NOT a second copy of the paper.

Sibling of ``_paper_read_html`` (keeps that file < 500 LOC). Renders, in
DECISION order (not data-production order): the read verdict (with the flag
reason inlined for a flagged paper), the ARR soundness-vs-relevance spine, a
6-cell goal board that is the single home of per-goal relevance, and a
self-explaining Quality panel. The panel answers "how was this judged and what
did we apply?" — a plain-language band gloss + a one-clause method line
(reference-free, author-blind, N-of-M self-consistency) + a legend, with the
decisive signals (red flags first) shown and the full rubric behind one
disclosure. Consumes the ``quality`` + ``goal_summaries`` cached by
``deep_review`` (``models.QualityEval`` / ``GoalSummary`` dumps).
"""
from __future__ import annotations

import html
import re
from typing import Any

from zotero_summarizer.services.library import _quality_prompts as qp

_BAND_LABEL = {"flag": "FLAG", "neutral": "NEUTRAL", "highlight": "HIGHLIGHT", "uncertain": "UNCERTAIN", "": "—"}
_STATE_LABEL = {"hit": "● addressed", "miss": "○ not addressed", "not_retrieved": "⚠ not retrieved"}

# The reference-free band, in one plain-language line (trusted constants → HTML).
_BAND_GLOSS = {
    "highlight": "<b>Rigorous enough to act on.</b> Passed our independent checks for evidence and reporting — read with confidence.",
    "flag": "<b>Read critically.</b> We found rigor problems that could make the headline result unreliable — see below.",
    "neutral": "<b>Sound but unremarkable.</b> No red flags, but it doesn't clear the bar for a confident highlight — judge it on relevance.",
    "uncertain": "<b>Needs your eyes.</b> Our independent passes disagreed on the verdict — don't trust the band alone.",
    "": "",
}
# The literal answer to "what did we apply?" — appended with "· N/M passes agree".
_METHOD_CLAUSE = (
    "How we judged it: a reference-free rubric + red-flag scan — no citation counts, "
    "authors hidden, scored only from what the paper itself shows"
)
_LEGEND = (
    "<b>FLAG</b> a rigor red-flag fired · <b>NEUTRAL</b> sound, unremarkable · "
    "<b>HIGHLIGHT</b> ≥6 grounded checks, zero flags"
)
# Short plain-English labels for the decisive-signal list (the full prompt
# questions live in _quality_prompts and drive the collapsed checklist).
_RUBRIC_LABEL = {
    "external_validation": "Validated on independent / held-out data",
    "uncertainty": "Reports uncertainty (CIs, error bars, seeds)",
    "ablation": "Ablations isolate what drives the result",
    "baselines": "Compared against fair, current baselines",
    "dataset_provenance": "States dataset source, version & license",
    "repro_detail": "Enough detail to reproduce",
    "code_data_released": "Code or data released",
    "patient_level_split": "Patient-level train/test split",
    "clinical_calibration": "Calibration / multi-site validation",
    "determinism": "Reports run-to-run determinism",
    "eval_contamination": "Addresses eval contamination",
}
# The rigor floor: a paper FLAGs if all three are absent (mirrors quality_eval._band_for).
_TRIAD = ("external_validation", "uncertainty", "ablation")


def _h(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _short_goal(goal: str, words: int = 4) -> str:
    parts = re.findall(r"[A-Za-z0-9/&-]+", str(goal or ""))
    short = " ".join(parts[:words])
    return short + ("…" if len(parts) > words else "")


def _relevance_verdict(n_fired: int, max_score: float) -> str:
    if n_fired and max_score >= 2.3:
        return "MUST READ"
    if n_fired and max_score >= 1.5:
        return "SHOULD READ"
    if n_fired:
        return "COULD READ"
    return "SKIP"


def _read_verdict(n_fired: int, band: str) -> tuple[str, str, str]:
    """(css_key, label, one-clause reason)."""
    if not n_fired:
        return "skip", "SKIP", "none of your research goals are addressed"
    if band == "flag":
        return "skim", "SKIM", "relevant to your goals but rigor is flagged — read critically"
    if band == "highlight":
        return "deep", "DEEP-READ", "relevant to your goals and rigorous"
    return "deep", "DEEP-READ", "relevant to your goals; quality is acceptable"


def brief_html(
    content: dict[str, Any],
    *,
    quality: dict[str, Any] | None,
    goal_summaries: list[dict[str, Any]] | None,
) -> str:
    """Verdict (loudest) → ARR spine → goal board. '' when no decision data
    exists (the brief silently degrades to the plain digest)."""
    goals = goal_summaries or []
    if not goals and not quality:
        return ""
    fired = [g for g in goals if g.get("retrieval_state") == "hit" and g.get("relevant")]
    n_fired = len(fired)
    max_score = max((float(g.get("score") or 0) for g in goals), default=0.0)
    band = str((quality or {}).get("quality_band") or "")
    agreed = int((quality or {}).get("passes_agreed") or 0)
    total = int((quality or {}).get("passes_total") or 0)

    # The verdict is the loudest line. For a flagged-but-relevant paper, inline
    # the actual red flag so the warning is readable without scrolling.
    vkey, vlabel, vreason = _read_verdict(n_fired, band)
    if band == "flag" and n_fired:
        rf = [str(x).strip() for x in ((quality or {}).get("red_flags") or []) if str(x).strip()]
        if rf:
            vreason = f"relevant, but rigor is FLAGGED — {rf[0]}. Read critically."
    verdict = f'<div class="verdict v-{vkey}"><b>{vlabel}</b> — {_h(vreason)}</div>' if (goals or quality) else ""

    passes = f"{agreed}/{total} agree" if total else ""
    rigor = (
        f'<div class="chip band-{_h(band or "none")}"><div class="chip-h">RIGOR</div>'
        f'<div class="chip-v">{_h(_BAND_LABEL.get(band, "—"))}'
        f'{f" · {passes}" if passes else ""}</div></div>'
    ) if quality else ""
    rel_verdict = _relevance_verdict(n_fired, max_score)
    relevance = (
        f'<div class="chip rel"><div class="chip-h">RELEVANCE</div>'
        f'<div class="chip-v">{_h(rel_verdict)} · {n_fired} goal{"s" if n_fired != 1 else ""} · '
        f'{max_score:.1f}/3</div></div>'
    )
    board = _goal_board_html(goals) if goals else ""
    return (
        '\n  <section class="brief fade-in">\n'
        f'    {verdict}\n'
        f'    <div class="spine">{rigor}{relevance}</div>\n'
        f'    {board}\n  </section>'
    )


def _goal_board_html(goals: list[dict[str, Any]]) -> str:
    """6-cell board — the SINGLE home of per-goal relevance. Fired cells carry
    the full grounded summary, the key sections to read, and the quote on demand."""
    cells = ""
    for g in goals:
        state = str(g.get("retrieval_state") or "not_retrieved")
        score = float(g.get("score") or 0.0)
        width = int(max(0.0, min(1.0, score / 3.0)) * 100)
        extra = ""
        if state == "hit":
            why = str(g.get("summary") or "").strip() or "relevant — grounded summary withheld"
            secs = ", ".join(_h(s) for s in (g.get("key_sections") or []) if str(s).strip())
            quotes = [str(q).strip() for q in (g.get("supporting_quotes") or []) if str(q).strip()]
            if secs:
                extra += f'<div class="g-sec">Read for you: {secs}</div>'
            if quotes:
                extra += f'<details class="g-quote"><summary>evidence</summary>“{_h(quotes[0])}”</details>'
        elif state == "miss":
            why = "not addressed in this paper"
        else:
            why = "retrieval degraded — not assessed"
        cells += (
            f'<div class="gcell state-{state}">'
            f'<div class="g-label">{_h(_short_goal(g.get("goal", "")))}</div>'
            f'<div class="g-state">{_h(_STATE_LABEL.get(state, state))}</div>'
            f'<div class="g-bar"><span style="width:{width}%"></span></div>'
            f'<div class="g-why">{_h(why)}</div>{extra}</div>'
        )
    return f'<div class="goal-board">{cells}</div>'


def _question_lookup() -> dict[str, str]:
    lookup = dict(qp.RUBRIC_ITEMS)
    for items in qp.DOMAIN_ITEMS.values():
        lookup.update(dict(items))
    return lookup


def _label(key: str) -> str:
    return _RUBRIC_LABEL.get(key, key.replace("_", " "))


def _decisive_rows(rubric: dict[str, str], band: str) -> tuple[str, list[tuple[bool, str]], str]:
    """(heading, [(passed, label)...], caption) — only the 2-3 items that moved
    the band, in plain English. Empty list ⇒ caller renders no decisive block."""
    core = [k for k, _ in qp.RUBRIC_ITEMS]
    val = {k: str(rubric.get(k) or "na").lower() for k in set(core) | set(_TRIAD) | set(rubric)}
    yes_count = sum(1 for k in core if val.get(k) == "yes")
    if band == "highlight":
        ordered = list(_TRIAD) + [k for k in core if k not in _TRIAD]
        earned = [k for k in ordered if val.get(k) == "yes"][:3]
        return "What earned it", [(True, _label(k)) for k in earned], f"{yes_count}/{len(core)} rigor checks met"
    if band == "flag":
        failed = [k for k in _TRIAD if val.get(k) != "yes"][:3]
        return "Why it sank", [(False, _label(k)) for k in failed], ""
    if band == "uncertain":
        return "Split decision", [(val.get(k) == "yes", _label(k)) for k in _TRIAD], ""
    gaps = [k for k in core if val.get(k) == "no"][:3]
    return "What's missing", [(False, _label(k)) for k in gaps], ""


def _full_checklist_html(rubric: dict[str, str], evidence: dict[str, Any]) -> str:
    """The complete rubric behind one disclosure — real questions, grounded quotes."""
    if not rubric:
        return ""
    lookup = _question_lookup()
    # Core rubric items first (prompt order), then domain items, then any leftovers.
    domain_keys = [k for items in qp.DOMAIN_ITEMS.values() for k, _ in items]
    ordered = [k for k, _ in qp.RUBRIC_ITEMS] + domain_keys + list(rubric)
    seen: set[str] = set()
    rows = ""
    for k in ordered:
        if k in seen or k not in rubric:
            continue
        seen.add(k)
        v = str(rubric.get(k) or "na").lower()
        question = lookup.get(k, k.replace("_", " "))
        quote = str(evidence.get(k) or "").strip()
        ev = f'<div class="rb-ev">“{_h(quote)}”</div>' if quote else ""
        rows += (
            f'<details class="rb-item rb-{v}"><summary><span class="rb-v">{_h(v)}</span> {_h(question)}</summary>'
            f'{ev}</details>'
        )
    return (
        f'<details class="q-full"><summary>Show the full {len(seen)}-point checklist</summary>'
        f'<div class="q-full-body">{rows}</div></details>'
    )


def quality_panel_html(quality: dict[str, Any] | None) -> str:
    """Self-explaining quality panel: band gloss + method clause + legend, then
    the decisive signals (red flags first), then the full rubric on demand."""
    if not quality:
        return ""
    band = str(quality.get("quality_band") or "")
    rubric = quality.get("rubric") or {}
    evidence = quality.get("evidence") or {}
    red_flags = [str(x).strip() for x in (quality.get("red_flags") or []) if str(x).strip()]
    overs = [str(x).strip() for x in (quality.get("overstatements") or []) if str(x).strip()]
    agreed = int(quality.get("passes_agreed") or 0)
    total = int(quality.get("passes_total") or 0)
    passes = f" · {agreed}/{total} passes agree" if total else ""

    signals = ""
    if red_flags:
        items = "".join(f"<li>{_h(x)}</li>" for x in red_flags[:3])
        signals += f'<div class="q-redflags"><div class="q-sig-h">⚠ Red flags</div><ul>{items}</ul></div>'
    heading, rows, caption = _decisive_rows(rubric, band)
    if rows:
        lis = "".join(
            f'<li class="q-d-{"ok" if ok else "no"}"><span class="q-mark">{"✓" if ok else "✗"}</span> {_h(text)}</li>'
            for ok, text in rows
        )
        cap = f'<div class="q-cap">{_h(caption)}</div>' if caption else ""
        signals += f'<div class="q-decisive"><div class="q-sig-h">{heading}</div><ul>{lis}</ul>{cap}</div>'
    if overs:
        items = "".join(f"<li>{_h(x)}</li>" for x in overs[:3])
        signals += f'<div class="q-overs"><div class="q-sig-h">Overstated claims</div><ul>{items}</ul></div>'

    return (
        '\n  <section id="quality" class="fade-in">\n'
        f'    <h2 class="q-title">Quality — {_h(_BAND_LABEL.get(band, "—"))}</h2>\n'
        f'    <div class="quality-card band-{_h(band or "none")}">\n'
        f'      <div class="q-gloss">{_BAND_GLOSS.get(band, "")}</div>\n'
        f'      <div class="q-method">{_h(_METHOD_CLAUSE)}{_h(passes)}</div>\n'
        f'      <div class="q-legend">{_LEGEND}</div>\n'
        f'      {signals}\n'
        f'      {_full_checklist_html(rubric, evidence)}\n'
        '    </div>\n  </section>'
    )


def brief_css() -> str:
    return """
.brief{margin:0 0 8px}
.verdict{border-radius:10px;padding:11px 15px;font-size:15px;margin-bottom:12px;border:1px solid var(--border)}
.verdict b{font-weight:800}
.v-deep{background:rgba(5,150,105,.1);border-color:#059669}
.v-skim{background:rgba(217,119,6,.12);border-color:#d97706}
.v-skip{background:rgba(148,163,184,.12)}
.spine{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.chip{border:1px solid var(--border);border-radius:10px;padding:11px 14px;background:var(--card)}
.chip-h{font-size:11px;font-weight:700;letter-spacing:.06em;color:var(--muted)}
.chip-v{font-size:15px;font-weight:700;margin-top:3px}
.chip.band-flag{border-color:#dc2626;background:rgba(220,38,38,.06)}
.chip.band-highlight{border-color:#059669;background:rgba(5,150,105,.07)}
.chip.band-uncertain{border-color:#d97706;background:rgba(217,119,6,.06)}
.goal-board{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.gcell{border:1px solid var(--border);border-radius:9px;padding:9px 10px;background:var(--card)}
.gcell.state-hit{border-left:3px solid #059669}
.gcell.state-miss{border-left:3px solid #94a3b8;opacity:.72}
.gcell.state-not_retrieved{border-left:3px solid #d97706;opacity:.66}
.g-label{font-weight:700;font-size:12px}
.g-state{font-size:11px;color:var(--muted);margin:2px 0}
.g-bar{height:5px;background:var(--border);border-radius:3px;overflow:hidden;margin:4px 0}
.g-bar span{display:block;height:100%;background:var(--accent)}
.g-why{font-size:12px;color:var(--text);line-height:1.4}
.g-sec{font-size:11px;color:var(--muted);margin-top:5px}
.g-quote{font-size:11px;color:var(--muted);margin-top:4px}
.g-quote summary{cursor:pointer;color:var(--accent);font-weight:600;list-style:none}
.g-quote summary::-webkit-details-marker{display:none}.g-quote summary::before{content:'“ ';opacity:.6}
.q-title{margin:26px 0 10px;font-size:19px;font-weight:600}
.quality-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;display:flex;flex-direction:column;gap:9px}
.quality-card.band-flag{border-color:#dc2626}
.quality-card.band-highlight{border-color:#059669}
.quality-card.band-uncertain{border-color:#d97706}
.q-gloss{font-size:15px;line-height:1.45}
.q-method{font-size:12px;color:var(--muted);line-height:1.4}
.q-legend{font-size:11px;color:var(--muted);border-top:1px solid var(--border);padding-top:8px}
.q-sig-h{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:3px}
.q-redflags{background:rgba(220,38,38,.07);border:1px solid #dc2626;border-radius:8px;padding:9px 12px}
.q-redflags .q-sig-h{color:#dc2626}
.q-redflags ul,.q-overs ul,.q-decisive ul{margin:0;padding-left:18px;font-size:13px;line-height:1.5}
.q-decisive ul{list-style:none;padding-left:0}
.q-decisive li{display:flex;gap:8px;align-items:baseline}
.q-mark{font-weight:800}.q-d-ok .q-mark{color:#059669}.q-d-no .q-mark{color:#dc2626}
.q-cap{font-size:12px;color:var(--muted);margin-top:4px}
.q-overs .q-sig-h{color:#d97706}
.q-full{border-top:1px solid var(--border);padding-top:8px}
.q-full>summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--accent);list-style:none}
.q-full>summary::-webkit-details-marker{display:none}.q-full>summary::before{content:'▸ '}
.q-full[open]>summary::before{content:'▾ '}
.q-full-body{display:flex;flex-direction:column;gap:6px;margin-top:8px}
.rb-item{border:1px solid var(--border);border-radius:7px;padding:6px 10px}
.rb-item summary{cursor:pointer;font-size:13px;list-style:none}.rb-item summary::-webkit-details-marker{display:none}
.rb-v{display:inline-block;min-width:30px;font-weight:700;font-size:11px;text-transform:uppercase;border-radius:4px;padding:1px 6px;margin-right:6px}
.rb-yes>summary .rb-v{background:rgba(5,150,105,.18);color:#059669}
.rb-no>summary .rb-v{background:rgba(220,38,38,.16);color:#dc2626}
.rb-na>summary .rb-v{background:var(--border);color:var(--muted)}
.rb-ev{font-size:12px;color:var(--muted);margin-top:6px;font-style:italic}
@media(max-width:600px){.spine,.goal-board{grid-template-columns:1fr}}
"""
