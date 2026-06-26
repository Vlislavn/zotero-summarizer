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
# Needle position (%) on the evidence-grade gauge — the band's calibrated place on
# the stain-uptake scale: low (eosin / tentative) → high (hematoxylin / strong).
_GAUGE_POS = {"flag": 18, "uncertain": 38, "neutral": 58, "highlight": 85, "": 50}

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
    "authors hidden, scored only from what the paper itself shows. Agreement is "
    "self-consistency across runs, not yet human-validated"
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


def _gloss(band: str, has_red_flags: bool) -> str:
    """Band gloss, DERIVED from the actual red-flag list so it never contradicts it.
    The plain ``neutral``/``highlight`` glosses say "No red flags" — only honest when
    none fired; when some did, say so instead (the user's contradiction bug)."""
    if has_red_flags and band in ("neutral", "highlight"):
        return ("<b>Mostly sound, with caveats.</b> It clears the bar overall, but our "
                "independent checks flagged the issues below — weigh them before relying on it.")
    return _BAND_GLOSS.get(band, "")


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

    # The verdict is the loudest line — the "diagnosis". For a flagged-but-relevant
    # paper, inline the actual red flag so the warning is readable without scrolling.
    vkey, vlabel, vreason = _read_verdict(n_fired, band)
    if band == "flag" and n_fired:
        rf = [str(x).strip() for x in ((quality or {}).get("red_flags") or []) if str(x).strip()]
        if rf:
            vreason = f"relevant, but rigor is FLAGGED — {rf[0]}. Read critically."
    # Relevance folds INTO the diagnosis reason line (the separate chip is gone).
    rel_verdict = _relevance_verdict(n_fired, max_score)
    rel_line = (
        f'<div class="v-rel">{_h(rel_verdict)} · {n_fired} goal{"s" if n_fired != 1 else ""} '
        f'matched · {max_score:.1f}/3</div>'
    )
    verdict = (
        f'<div class="verdict v-{vkey}"><div class="v-eyebrow">Diagnosis</div>'
        f'<div class="v-word">{_h(vlabel)}</div>'
        f'<div class="v-why">{_h(vreason)}</div>{rel_line}</div>'
    ) if (goals or quality) else ""

    gauge = _gauge_html(band, agreed, total) if quality else ""
    board = _goal_board_html(goals) if goals else ""
    return (
        '\n  <section class="brief fade-in focus-in">\n'
        f'    {verdict}\n'
        f'    {gauge}\n'
        f'    {board}\n  </section>'
    )


def _gauge_html(band: str, agreed: int, total: int) -> str:
    """The single evidence-grade gauge that REPLACES the RIGOR/RELEVANCE chips: a
    calibrated eosin→hematoxylin track (low→high stain uptake = evidence strength)
    with a teal needle placed by band, the band label, and the N/M passes. It OWNS
    the band interpretation (a position on a scale) so the reader doesn't decode it."""
    pct = _GAUGE_POS.get(band, 50)
    passes = f"{agreed}/{total} passes agree" if total else ""
    passes_html = f'<span class="gauge-passes">{_h(passes)}</span>' if passes else ""
    return (
        '<div class="gauge"><div class="gauge-h">Evidence grade</div>'
        f'<div class="gauge-track"><span class="gauge-needle" style="left:{pct}%"></span></div>'
        '<div class="gauge-scale"><span>Tentative</span><span>Moderate</span><span>Strong</span></div>'
        f'<div class="gauge-read"><span class="gauge-band">{_h(_BAND_LABEL.get(band, "—"))}</span>'
        f'{passes_html}</div>'
        '<div class="gauge-method">reference-free · author-blind · self-consistency</div></div>'
    )


def _goal_board_html(goals: list[dict[str, Any]]) -> str:
    """6-cell board — the SINGLE home of per-goal relevance. A HIT-and-relevant cell
    is "stained" (eosin wash) and binds its summary (the claim) to its supporting
    quote (the evidence) via the hematoxylin tether rail; the quote is surfaced
    inline so the binding reads at a glance (Uniform Connectedness). Miss / not-
    retrieved cells stay "unstained" — tissue that didn't take the stain."""
    cells = ""
    for g in goals:
        state = str(g.get("retrieval_state") or "not_retrieved")
        score = float(g.get("score") or 0.0)
        width = int(max(0.0, min(1.0, score / 3.0)) * 100)
        is_hit = state == "hit" and bool(g.get("relevant"))
        extra, has_ev = "", ""
        if state == "hit":
            why = str(g.get("summary") or "").strip() or "relevant — grounded summary withheld"
            secs = ", ".join(_h(s) for s in (g.get("key_sections") or []) if str(s).strip())
            quotes = [str(q).strip() for q in (g.get("supporting_quotes") or []) if str(q).strip()]
            if secs:
                extra += f'<div class="g-sec">Read for you: {secs}</div>'
            if quotes:
                has_ev = " has-evidence"  # gates the tether rail (only when evidence exists)
                extra += f'<div class="g-quote">“{_h(quotes[0])}”</div>'
        elif state == "miss":
            why = "not addressed in this paper"
        else:
            why = "retrieval degraded — not assessed"
        stain = "stained" if is_hit else "unstained"
        cells += (
            f'<div class="gcell state-{state} {stain}{has_ev}">'
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
        signals += f'<div class="q-overs unstained"><div class="q-sig-h">Overstated claims</div><ul>{items}</ul></div>'

    return (
        '\n  <section id="quality" class="fade-in">\n'
        f'    <h2 class="q-title">Quality</h2>\n'
        f'    <div class="quality-card band-{_h(band or "none")}">\n'
        f'      <div class="q-gloss">{_gloss(band, bool(red_flags))}</div>\n'
        f'      <div class="q-method">{_h(_METHOD_CLAUSE)}{_h(passes)}</div>\n'
        f'      <div class="q-legend">{_LEGEND}</div>\n'
        f'      {signals}\n'
        f'      {_full_checklist_html(rubric, evidence)}\n'
        '    </div>\n  </section>'
    )


def brief_css() -> str:
    """H&E "specimen slide" styling. The verdict is the one loud element (Von
    Restorff); the gauge OWNS band interpretation (Tesler); a stained goal cell
    binds claim→evidence with the hematoxylin tether (Uniform Connectedness).
    Palette/font vars come from `_paper_read_html._css` (concatenated before this)."""
    return """
.brief{margin:0 0 10px}
.verdict{border:1px solid var(--border);border-left:4px solid var(--muted);border-radius:12px;padding:18px 22px;margin-bottom:18px;background:var(--card);box-shadow:var(--shadow)}
.v-eyebrow{font-family:var(--font-display);font-size:11px;font-weight:600;letter-spacing:.2em;text-transform:uppercase;color:var(--hema)}
.v-word{font-family:var(--font-display);font-size:clamp(25px,4.2vw,38px);font-weight:500;line-height:1;letter-spacing:0;text-transform:uppercase;margin:6px 0 8px}
.v-why{font-family:var(--font-read);font-size:16px;line-height:1.5;color:var(--text)}
.v-rel{font-family:var(--font-mono);font-size:12px;color:var(--muted);margin-top:8px;letter-spacing:.02em}
.v-deep{border-left-color:var(--readout)}.v-deep .v-word{color:var(--readout)}
.v-skim{border-left-color:var(--caution)}.v-skim .v-word{color:var(--caution)}
.v-skip{border-left-color:var(--muted)}.v-skip .v-word{color:var(--muted)}
.gauge{border:1px solid var(--border);border-radius:12px;padding:15px 18px;margin-bottom:16px;background:var(--card);box-shadow:var(--shadow)}
.gauge-h{font-family:var(--font-display);font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.gauge-track{position:relative;height:9px;border-radius:5px;margin:11px 0 6px;background:linear-gradient(90deg,var(--eosin) 0%,var(--hair) 50%,var(--hema) 100%)}
.gauge-needle{position:absolute;top:-4px;width:2px;height:17px;background:var(--text)}
.gauge-needle::after{content:"";position:absolute;top:-3px;left:-3px;width:8px;height:8px;background:var(--text);transform:rotate(45deg)}
.gauge-scale{display:flex;justify-content:space-between;font-family:var(--font-mono);font-size:11px;color:var(--muted)}
.gauge-read{display:flex;align-items:baseline;gap:10px;margin-top:9px}
.gauge-band{font-family:var(--font-display);font-size:14px;font-weight:500;letter-spacing:.02em;color:var(--text)}
.gauge-passes{font-family:var(--font-mono);font-size:12px;color:var(--muted)}
.gauge-method{font-family:var(--font-mono);font-size:11px;color:var(--muted);margin-top:5px;letter-spacing:.01em}
.goal-board{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:11px;margin-bottom:6px}
.gcell{position:relative;border:1px solid var(--border);border-radius:11px;padding:12px 13px 13px;background:var(--card);box-shadow:var(--shadow)}
.gcell.stained{border-left:3px solid var(--hema)}
.gcell.unstained{border-left:2px dotted var(--hair);opacity:.72}
.gcell.state-not_retrieved{opacity:.6}
.g-label{font-family:var(--font-display);font-weight:600;font-size:13px;line-height:1.3;color:var(--text)}
.g-state{font-family:var(--font-mono);font-size:11px;color:var(--muted);margin:4px 0}
.g-bar{height:5px;background:var(--hair);border-radius:3px;overflow:hidden;margin:5px 0 7px}
.g-bar span{display:block;height:100%;background:linear-gradient(90deg,var(--eosin),var(--hema))}
.g-why{font-family:var(--font-read);font-size:14px;color:var(--text);line-height:1.5}
.g-sec{font-family:var(--font-mono);font-size:11px;color:var(--muted);margin-top:8px;letter-spacing:.01em}
.g-quote{font-family:var(--font-read);font-style:italic;font-size:13px;line-height:1.5;color:var(--text);margin-top:9px;padding:8px 11px;border-radius:8px;background:var(--eosin-wash)}
.gcell.has-evidence .g-quote{position:relative;margin-left:11px}
.gcell.has-evidence .g-quote::before{content:"";position:absolute;left:-11px;top:-9px;bottom:7px;width:2px;background:var(--hema);border-radius:1px;transform-origin:top}
.gcell.has-evidence .g-quote::after{content:"";position:absolute;left:-14px;top:13px;width:8px;height:8px;border-radius:50%;background:var(--hema)}
.q-title{font-family:var(--font-display);margin:30px 0 12px;font-size:13px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.quality-card{background:var(--card);border:1px solid var(--border);border-left:4px solid var(--muted);border-radius:12px;padding:20px;display:flex;flex-direction:column;gap:14px;box-shadow:var(--shadow)}
.quality-card.band-flag{border-left-color:var(--alert)}
.quality-card.band-highlight{border-left-color:var(--readout)}
.quality-card.band-uncertain{border-left-color:var(--caution)}
.q-gloss{font-family:var(--font-read);font-size:15px;line-height:1.5;color:var(--text)}
.q-method{font-family:var(--font-mono);font-size:12px;color:var(--muted);line-height:1.5}
.q-legend{font-family:var(--font-mono);font-size:11px;color:var(--muted);border-top:1px solid var(--hair);padding-top:11px}
.q-sig-h{font-family:var(--font-display);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:6px}
.q-redflags{background:var(--alert-wash);border:1px solid var(--alert);border-radius:9px;padding:11px 14px}
.q-redflags .q-sig-h{color:var(--alert)}
.q-redflags ul,.q-overs ul,.q-decisive ul{margin:0;padding-left:18px;font-family:var(--font-read);font-size:14px;line-height:1.55;color:var(--text)}
.q-decisive,.q-overs{border-top:1px solid var(--hair);padding-top:12px}
.q-decisive ul{list-style:none;padding-left:0}
.q-decisive li{display:flex;gap:9px;align-items:baseline;padding:2px 0}
.q-mark{font-family:var(--font-mono);font-weight:500}.q-d-ok .q-mark{color:var(--readout)}.q-d-no .q-mark{color:var(--alert)}
.q-cap{font-family:var(--font-mono);font-size:12px;color:var(--muted);margin-top:6px}
.q-overs .q-sig-h{color:var(--caution)}
.q-overs.unstained li{text-decoration:underline dotted var(--hair);text-underline-offset:3px}
.q-full{border-top:1px solid var(--hair);padding-top:11px}
.q-full>summary{cursor:pointer;font-family:var(--font-mono);font-size:13px;font-weight:500;color:var(--readout);list-style:none}
.q-full>summary::-webkit-details-marker{display:none}.q-full>summary::before{content:'▸ '}
.q-full[open]>summary::before{content:'▾ '}
.q-full-body{display:flex;flex-direction:column;gap:4px;margin-top:10px}
.rb-item{border-bottom:1px solid var(--hair);padding:8px 2px}
.rb-item:last-child{border-bottom:none}
.rb-item summary{cursor:pointer;font-family:var(--font-read);font-size:14px;line-height:1.45;list-style:none;color:var(--text)}.rb-item summary::-webkit-details-marker{display:none}
.rb-v{display:inline-block;min-width:30px;text-align:center;font-family:var(--font-mono);font-weight:500;font-size:10px;text-transform:uppercase;border-radius:4px;padding:2px 6px;margin-right:8px}
.rb-yes>summary .rb-v{background:var(--accent-soft);color:var(--readout)}
.rb-no>summary .rb-v{background:var(--alert-wash);color:var(--alert)}
.rb-na>summary .rb-v{background:var(--hair);color:var(--muted)}
.rb-ev{font-family:var(--font-read);font-size:13px;color:var(--muted);margin-top:7px;font-style:italic;line-height:1.5}
@media(prefers-reduced-motion:no-preference){.gcell.has-evidence .g-quote::before{animation:draw .6s ease .25s both}@keyframes draw{from{transform:scaleY(0)}to{transform:scaleY(1)}}}
@media(max-width:600px){.goal-board{grid-template-columns:1fr}.quality-card{padding:16px}}
"""
