"""Markdown, HTML and audit output for paper-read artifacts."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import now_iso_z


def _paper_short_name(title: str, fallback: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    ignored = {"a", "an", "the", "of", "for", "with", "and", "paper"}
    selected = [w for w in words if w.lower() not in ignored][:4]
    return "_".join(selected) or re.sub(r"[^A-Za-z0-9]+", "_", fallback).strip("_") or "paper"


def write_outputs(
    pdf_path: Path,
    content: dict[str, Any],
    *,
    digest: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    goal_summaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write paper-render-compatible notes, presentation and audit files."""
    short = _paper_short_name(str(content.get("title") or ""), pdf_path.stem)
    notes_path = pdf_path.parent / f"{short}_notes.md"
    html_path = pdf_path.parent / f"{short}_presentation.html"
    notes_path.write_text(_render_notes(content, digest, quality, goal_summaries), encoding="utf-8")
    html_path.write_text(_render_presentation(content, short, digest, quality, goal_summaries), encoding="utf-8")
    audit = _audit_presentation(html_path, notes_path, content.get("figures") or [])
    audit_path = pdf_path.parent / f"{short}_audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "paper_name": short,
        "notes_path": str(notes_path),
        "presentation_path": str(html_path),
        "audit_path": str(audit_path),
        "figures_dir": str(pdf_path.parent / "figures"),
        "audit": audit,
    }


def _render_notes(
    content: dict[str, Any],
    digest: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    goal_summaries: list[dict[str, Any]] | None = None,
) -> str:
    title = str(content.get("title") or "Untitled")
    authors = str(content.get("authors") or "Unknown authors")
    keywords = ", ".join(content.get("keywords") or [])
    figures = list(content.get("figures") or [])
    lines: list[str] = [
        f"# {title}", "",
        f"> **Authors**: {authors}", ">",
        f"> **Keywords**: {keywords or 'not provided'}", ">",
        f"> **Source tier**: {content.get('source_tier', 'pdf')}", "",
        "---", "",
    ]
    if digest:
        if digest.get("tldr"):
            lines += ["## TL;DR", "", str(digest["tldr"]), ""]
        if digest.get("executive_summary"):
            lines += ["## Executive Summary", "", str(digest["executive_summary"]), ""]
        decision = str(digest.get("read_decision") or "")
        why = str(digest.get("read_why") or "")
        if decision:
            lines += [f"## Read decision: {decision.upper()}{f' — {why}' if why else ''}", ""]
        for label, key in [
            ("Relevance", "relevance"), ("Controversies", "controversies"),
            ("Industry impact", "industry_impact"), ("Academy impact", "academy_impact"),
            ("Impact", "impact"), ("Methods", "methods"), ("Limitations", "limitations"),
            ("Unknown unknowns", "unknown_unknowns"),
        ]:
            val = str(digest.get(key) or "")
            if val:
                lines += [f"## {label}", "", val, ""]
        for label, key in [
            ("Key findings", "key_findings"), ("Read parts", "read_parts"),
            ("Implementation", "implementation"),
        ]:
            items = [str(x) for x in (digest.get(key) or []) if x]
            if items:
                lines += [f"## {label}", ""] + [f"- {x}" for x in items] + [""]
        strength = str(digest.get("key_strength") or "")
        weakness = str(digest.get("key_weakness") or "")
        if strength:
            lines += [f"**Strength**: {strength}", ""]
        if weakness:
            lines += [f"**Weakness**: {weakness}", ""]
    else:
        lines += ['*Deep review not yet run — use "Run deeper review" to generate a digest.*', ""]
    lines += _notes_quality_and_goals(quality, goal_summaries)
    body_sections = [
        s for s in (content.get("render_sections") or content.get("sections") or [])
        if str(s.get("text") or "").strip()
    ]
    if body_sections:
        lines += ["## Sections", ""]
        for sec in body_sections:
            lines += [f"### {sec.get('title') or 'Section'}", "", _reflow_prose(str(sec.get("text") or "")), ""]
    lines += [
        "## Quick Reference", "",
        "| Item | Value |", "|---|---|",
        f"| Pages | {content.get('n_pages', 0)} |",
        f"| Figures | {len([f for f in figures if f.get('name')])} |",
        f"| References | {content.get('references_count', 0)} |", "",
    ]
    if figures:
        lines += ["## Figures", ""]
        for idx, fig in enumerate(figures, start=1):
            caption = fig.get("caption") or fig.get("label") or f"Figure {idx}"
            lines.append(f"> **[Figure {idx}: {caption}]**")
    return "\n".join(lines).strip() + "\n"


def _notes_quality_and_goals(
    quality: dict[str, Any] | None, goal_summaries: list[dict[str, Any]] | None
) -> list[str]:
    """Quality verdict + per-goal relevance as markdown (empty when absent)."""
    lines: list[str] = []
    if quality and quality.get("quality_band"):
        lines += [f"## Quality: {str(quality['quality_band']).upper()}", ""]
        rubric = quality.get("rubric") or {}
        if rubric:
            lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in rubric.items()] + [""]
        for label, key in [("Red flags", "red_flags"), ("Overstated claims", "overstatements")]:
            items = [str(x) for x in (quality.get(key) or []) if x]
            if items:
                lines += [f"**{label}**:", ""] + [f"- {x}" for x in items] + [""]
    fired = [g for g in (goal_summaries or [])
             if g.get("retrieval_state") == "hit" and g.get("relevant") and g.get("summary")]
    if fired:
        lines += ["## Relevance to your goals", ""]
        for g in fired:
            lines += [f"### {g.get('goal')}", "", str(g.get("summary")), ""]
            secs = ", ".join(g.get("key_sections") or [])
            if secs:
                lines += [f"*Key sections for you: {secs}*", ""]
    return lines


def _render_presentation(
    content: dict[str, Any],
    short_name: str,
    digest: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    goal_summaries: list[dict[str, Any]] | None = None,
) -> str:
    from zotero_summarizer.services.library import _paper_read_brief as brief

    title = str(content.get("title") or "Untitled")
    authors = str(content.get("authors") or "")
    figures = [fig for fig in (content.get("figures") or []) if fig.get("name")]
    image_map = {f"ph-fig{i}": f"figures/{fig['name']}" for i, fig in enumerate(figures, 1)}
    brief_block = brief.brief_html(content, quality=quality, goal_summaries=goal_summaries)
    quality_block = brief.quality_panel_html(quality)
    empty_state = (
        '<section class="fade-in"><div class="empty-state">No readable content could be '
        "extracted from this PDF (no figures or digest). It may be a scanned or image-only "
        "document — open the original PDF in Zotero, or run a deep review to add a digest."
        "</div></section>"
        if not figures and not digest and not quality and not (goal_summaries or []) else ""
    )
    tldr_html = f'<p class="tldr">{_h(str(digest["tldr"]))}</p>' if (digest and digest.get("tldr")) else ""
    refs = int(content.get("references_count") or 0)
    foot_meta = f"{int(content.get('n_pages') or 0)} pages · {len(figures)} figures · {refs} references"

    # Decision order: verdict+spine+board → quality (always visible) → then
    # reference material (digest, figures) folded below. The full paper body is
    # NOT embedded — this brief is a triage aid; the PDF lives in Zotero.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_h(short_name)} — Paper Brief</title>
<style>{_css()}{brief.brief_css()}</style>
</head>
<body>
<div id="progress"></div>
<div class="top-ctrl"><button class="ctrl-btn" onclick="toggleTheme()">Theme</button></div>
<main class="content">
  <header class="hero fade-in">
    <h1>{_h(title)}</h1>
    <div class="subtitle">{_h(authors)}</div>
    {tldr_html}
  </header>
  {brief_block}
  {quality_block}
  {empty_state}
  {_digest_section_html(digest) if digest else ''}
  {_figures_section_html(figures)}
  <footer>{_h(foot_meta)} · open the original PDF in Zotero for the full text</footer>
</main>
<button class="back-top" id="back-top" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
<script>const imageMap = {json.dumps(image_map, ensure_ascii=False)};</script>
<script>{_js()}</script>
</body>
</html>
"""


def _digest_section_html(digest: dict[str, Any]) -> str:
    def _row(label: str, key: str) -> str:
        val = str(digest.get(key) or "")
        return f'<div class="drow"><span class="dlbl">{label}</span><span>{_h(val)}</span></div>' if val else ""

    def _bullets(label: str, key: str) -> str:
        items = [str(x) for x in (digest.get(key) or []) if x]
        if not items:
            return ""
        inner = "".join(f"<li>{_h(x)}</li>" for x in items)
        return f'<div class="drow"><span class="dlbl">{label}</span><ul class="dbullets">{inner}</ul></div>'

    def _group(title: str, body: str, open_: bool = False) -> str:
        if not body:
            return ""
        attr = " open" if open_ else ""
        return (
            f'<details{attr} class="dgroup"><summary class="dgroup-hdr">{title}</summary>'
            f'<div class="dgroup-body">{body}</div></details>'
        )

    summary_body = "".join(filter(None, [
        _row("Executive summary", "executive_summary"),
        _bullets("Key findings", "key_findings"),
    ]))
    strength = str(digest.get("key_strength") or "")
    weakness = str(digest.get("key_weakness") or "")
    assess = [_row("Why read", "read_why"), _row("Controversies", "controversies")]
    if strength:
        assess.append(f'<div class="drow"><span class="dlbl">Strength</span><span class="dpos">{_h(strength)}</span></div>')
    if weakness:
        assess.append(f'<div class="drow"><span class="dlbl">Weakness</span><span class="dneg">{_h(weakness)}</span></div>')
    methods_body = "".join(filter(None, [_row("Methods", "methods"), _row("Limitations", "limitations")]))
    impact_body = "".join(filter(None, [
        _row("Industry", "industry_impact"), _row("Academia", "academy_impact"),
        _row("Unknown unknowns", "unknown_unknowns"),
        _bullets("Read parts", "read_parts"), _bullets("Implementation", "implementation"),
    ]))
    blocks = "".join(filter(None, [
        _group("Summary", summary_body, open_=True),
        _group("Assessment", "".join(filter(None, assess))),
        _group("Methods & limits", methods_body),
        _group("Impact & action", impact_body),
    ]))
    verdict = str(digest.get("verdict") or "")
    if not blocks and not verdict:
        return ""
    # Collapsed by default — the referee verdict shows as the header so the one
    # decision-useful sentence is visible at a glance without the prose wall.
    head = _h(verdict) if verdict else "Summary, assessment, methods, impact"
    return f"""
  <details id="digest" class="fade-in digest-fold">
    <summary class="fold-h"><span class="fold-tag">DIGEST</span>{head}</summary>
    <div class="digest-card">{blocks}</div>
  </details>"""


def _figures_section_html(figures: list[dict[str, Any]]) -> str:
    if not figures:
        return ""
    fig_html = ""
    for idx, fig in enumerate(figures, start=1):
        caption = str(fig.get("caption") or fig.get("label") or f"Figure {idx}")
        fig_html += f"""
    <div class="fig-card" id="ph-fig{idx}">
      <div class="ph-label">Figure {idx}</div>
      <div class="ph-caption">{_h(caption)}</div>
      <div class="ph-filename">figures/{_h(fig['name'])}</div>
    </div>"""
    return f"""
  <section id="figures" class="fade-in">
    <h2 class="sec-title">Figures ({len(figures)})</h2>
    <div class="fig-gallery">{fig_html}</div>
  </section>"""


def _reflow_prose(text: str) -> str:
    """Turn PDF hard-wrapped lines into flowing prose: de-hyphenate words split
    across a line break and collapse intra-paragraph newlines, while keeping
    blank-line paragraph breaks. Used by the notes.md artifact (the brief no
    longer embeds the paper body)."""
    text = (text or "").replace("\r\n", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate "in-\nformation"
    paragraphs = re.split(r"\n\s*\n", text)
    return "\n\n".join(" ".join(p.split()) for p in paragraphs if p.strip())


def _audit_presentation(
    html_path: Path,
    notes_path: Path,
    figures: list[dict[str, Any]],
) -> dict[str, Any]:
    html_text = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
    notes_text = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
    blocking: list[str] = []
    minor: list[str] = []

    if not notes_text.strip():
        blocking.append("notes file is empty")
    if "const imageMap" not in html_text:
        blocking.append("HTML missing imageMap")
    if "<section" not in html_text:
        blocking.append("HTML has no sections")

    named = [f for f in figures if f.get("name")]
    all_figures = [f for f in figures if f.get("caption") or f.get("label")]
    if all_figures and not named:
        blocking.append(f"all {len(all_figures)} figures are placeholders — no images generated")
    for idx, fig in enumerate(named, start=1):
        if f'id="ph-fig{idx}"' not in html_text:
            blocking.append(f"missing placeholder ph-fig{idx} for {fig['name']}")

    if notes_text and re.search(r"\bAuthor\s+\d+", notes_text):
        minor.append("notes contain placeholder authors (Author N) — Zotero fallback may not have run")
    # Residual LaTeX (\cmd or $…$) means extraction degraded. A handful is minor;
    # egregious leakage is a blocking signal to rebuild after fixing extraction.
    latex_hits = len(re.findall(r"\\[a-zA-Z]{2,}|\$[^$\n]{1,80}\$", notes_text or ""))
    if latex_hits > 8:
        blocking.append(f"notes contain {latex_hits} residual LaTeX fragments — extraction degraded, rebuild")
    elif latex_hits:
        minor.append("notes contain residual LaTeX commands")

    return {
        "status": "passed" if not blocking else "blocking",
        "blocking": blocking,
        "minor": minor,
        "checked_at": now_iso_z(),
    }


def _h(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def _css() -> str:
    return """
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300;9..144,400;9..144,500&family=Inter:wght@300;400;500;600&display=swap');
:root{--bg:#fffefc;--card:#f3f8f3;--text:#171614;--muted:#585650;--accent:#0f3e17;--accent-soft:rgba(15,62,23,.09);--border:#e5e7eb;--hair:rgba(23,22,20,.12);--shadow:none;--hema:#0f3e17;--eosin:#6aa97b;--eosin-deep:#225f38;--eosin-wash:rgba(177,219,184,.42);--caution:#8a6a2a;--caution-wash:rgba(212,178,116,.22);--alert:#8c4330;--alert-wash:rgba(212,147,127,.2);--glass:#fffefc;--frost:#f1f7f2;--readout:#0f3e17;--font-serif:"Fraunces",Georgia,serif;--font-display:"Inter",system-ui,-apple-system,Segoe UI,sans-serif;--font-read:"Inter",system-ui,-apple-system,Segoe UI,sans-serif;--font-mono:"Inter",ui-monospace,SFMono-Regular,Menlo,monospace}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.65 var(--font-read);letter-spacing:-.011em;-webkit-font-smoothing:antialiased}
body.dark{--bg:#0e140f;--card:#16201a;--text:#eef3ee;--muted:#9baf9f;--accent:#7fc08e;--accent-soft:rgba(127,192,142,.14);--border:#26342a;--hair:rgba(238,243,238,.13);--shadow:none;--hema:#7fc08e;--eosin:#7fc08e;--eosin-deep:#a7d6b0;--eosin-wash:rgba(127,192,142,.16);--caution:#d9b46e;--caution-wash:rgba(217,180,110,.16);--alert:#d59079;--alert-wash:rgba(213,144,127,.16);--glass:#0e140f;--frost:#16201a;--readout:#7fc08e}
#progress{position:fixed;top:0;left:0;width:0;height:2px;background:var(--readout);z-index:10;transition:width .1s linear}
.top-ctrl{position:fixed;right:18px;top:14px;z-index:5}
.ctrl-btn{font-family:var(--font-mono);border:1px solid var(--border);background:var(--card);color:var(--muted);border-radius:8px;padding:6px 13px;cursor:pointer;font-size:12px;box-shadow:var(--shadow)}
.ctrl-btn:hover{background:var(--hema);color:#fff;border-color:var(--hema)}
.content{max-width:min(960px,92vw);margin:0 auto;padding:36px 28px 80px}
.hero{padding:24px 26px;border-radius:14px;background:var(--frost);border:1px solid var(--border);border-bottom:2px solid var(--hema);margin-bottom:24px;box-shadow:var(--shadow)}
.hero::before{content:"Paper brief";display:block;font-family:var(--font-display);font-size:11px;font-weight:600;letter-spacing:.2em;text-transform:uppercase;color:var(--hema);margin-bottom:10px}
.hero h1{margin:0 0 8px;font-family:var(--font-serif);font-size:clamp(24px,3vw,34px);line-height:1.18;font-weight:300;letter-spacing:-.015em;text-wrap:balance;color:var(--text)}
.subtitle{font-family:var(--font-mono);font-size:13px;color:var(--muted)}
.tldr{margin-top:12px;font-size:15.5px;font-style:italic;color:var(--text);line-height:1.55}
.sec-title{font-family:var(--font-display);margin:30px 0 12px;font-size:13px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.digest-fold{margin:22px 0}
.fold-h{cursor:pointer;font-family:var(--font-display);font-size:14px;font-weight:600;color:var(--text);list-style:none;padding:13px 16px;border:1px solid var(--border);border-radius:12px;background:var(--card);box-shadow:var(--shadow);display:flex;gap:11px;align-items:baseline;line-height:1.4}
.fold-h::-webkit-details-marker{display:none}.fold-h::after{content:'▸';margin-left:auto;color:var(--muted);font-size:12px}
.digest-fold[open]>.fold-h{border-radius:12px 12px 0 0;box-shadow:none}
.digest-fold[open]>.fold-h::after{content:'▾'}
.fold-tag{font-family:var(--font-mono);font-size:10px;font-weight:600;letter-spacing:.06em;color:var(--hema);background:var(--accent-soft);border-radius:5px;padding:2px 7px;flex-shrink:0}
.digest-card{background:var(--card);border:1px solid var(--border);border-top:none;border-radius:0 0 12px 12px;padding:18px 20px;display:flex;flex-direction:column;gap:14px}
.drow{display:grid;grid-template-columns:128px 1fr;gap:6px 16px;align-items:start;line-height:1.55}
.dlbl{font-family:var(--font-mono);font-size:11px;letter-spacing:.02em;text-transform:uppercase;color:var(--muted);padding-top:1px}
.drow span:not(.dlbl){font-size:15px}.dbullets{margin:0;padding-left:18px;font-size:15px;line-height:1.55}
.dpos{color:var(--readout);font-size:15px}.dneg{color:var(--alert);font-size:15px}
.dgroup{border:none;padding:0;margin:0;border-top:1px solid var(--hair);padding-top:12px}
.dgroup:first-child{border-top:none;padding-top:0}
.dgroup-hdr{font-family:var(--font-display);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--hema);cursor:pointer;padding:2px 0;user-select:none;list-style:none}
.dgroup-hdr::-webkit-details-marker,.dgroup-hdr::marker{display:none}.dgroup-hdr::before{content:'▸ ';font-size:10px}
details[open]>.dgroup-hdr::before{content:'▾ '}.dgroup-body{padding:10px 0 2px;display:flex;flex-direction:column;gap:12px}
.empty-state{background:var(--card);border:1px dashed var(--border);border-radius:12px;padding:28px;text-align:center;color:var(--muted);font-size:14px;line-height:1.55}
.fig-gallery{display:flex;flex-direction:column;gap:18px}
.fig-card{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;box-shadow:var(--shadow)}
.fig-img{width:100%;display:block;border-bottom:1px solid var(--hair)}
.fig-caption{padding:11px 16px}.ph-label{font-family:var(--font-mono);font-weight:600;font-size:11px;letter-spacing:.03em;text-transform:uppercase;color:var(--hema)}
.ph-caption{color:var(--text);font-size:14px;margin-top:3px;line-height:1.5}
.ph-filename{color:var(--muted);font-size:11px;margin-top:4px;font-family:var(--font-mono)}
footer{text-align:center;color:var(--muted);border-top:1px solid var(--hair);margin-top:48px;padding-top:22px;font-family:var(--font-mono);font-size:12px;line-height:1.6}
.back-top{display:none;position:fixed;bottom:24px;right:20px;background:var(--hema);color:#fff;border:none;border-radius:50%;width:42px;height:42px;font-size:18px;cursor:pointer;z-index:5;align-items:center;justify-content:center;box-shadow:0 4px 12px rgba(0,0,0,.2)}
.back-top.show{display:flex}
.fade-in,.focus-in{opacity:1}
@media(prefers-reduced-motion:no-preference){.fade-in{animation:fade .25s ease both}.focus-in{animation:focus .55s cubic-bezier(.2,.7,.2,1) both}@keyframes fade{from{opacity:.4;transform:translateY(4px)}to{opacity:1;transform:none}}@keyframes focus{from{opacity:0;filter:blur(5px);transform:translateY(8px)}to{opacity:1;filter:none;transform:none}}}
@media(max-width:760px){.drow{grid-template-columns:1fr;gap:2px 0}}
@media(max-width:600px){.content{padding:22px 14px 56px}.hero{padding:20px 18px}.hero h1{font-size:22px}}
@media print{#progress,.top-ctrl,.back-top{display:none!important}.fade-in,.focus-in{animation:none}body{background:#fff;color:#000}.hero{box-shadow:none}.fig-card,.fold-h{box-shadow:none}}
"""


def _js() -> str:
    return """
function toggleTheme(){document.body.classList.toggle('dark');localStorage.setItem('pt',document.body.classList.contains('dark')?'dark':'light')}
if(localStorage.getItem('pt')==='dark')document.body.classList.add('dark');
const prog=document.getElementById('progress');const bt=document.getElementById('back-top');
window.addEventListener('scroll',()=>{const d=document.documentElement;const pct=d.scrollHeight-d.clientHeight;if(pct>0)prog.style.width=(d.scrollTop/pct*100)+'%';bt.classList.toggle('show',window.scrollY>400)},{passive:true});
if(typeof imageMap!=='undefined'){Object.entries(imageMap).forEach(([id,src])=>{const el=document.getElementById(id);if(!el)return;const label=(el.querySelector('.ph-label')?.textContent||'').trim();const caption=(el.querySelector('.ph-caption')?.textContent||'').trim();/* drop the label line when the caption already begins with it (e.g. label "Figure 1" + caption "Figure 1: …") so each caption shows once */const showLabel=label&&!caption.toLowerCase().startsWith(label.toLowerCase());el.outerHTML=`<div class="fig-card"><img class="fig-img" loading="lazy" src="${src}" alt="${caption}"><div class="fig-caption">${showLabel?`<div class="ph-label">${label}</div>`:''}<div class="ph-caption">${caption}</div></div></div>`})}
"""
