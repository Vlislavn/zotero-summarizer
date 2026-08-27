"""Zotero-renderable note HTML builders (triage / verdict / digest).

Zotero's TinyMCE editor silently strips most HTML — no CSS, no <div>, no <h1>.
These builders use ONLY <h2>, <p>, <ul>/<li>, <strong>, <em>, so they are the
single source of truth for note markup. Each note is led by an HTML-comment
provenance marker that survives TinyMCE round-trips (verified against the
user's prior agent notes).
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from zotero_summarizer.models import SummarizeResponse

# Provenance constants: how the user (and future agents) tell agent-written
# notes from hand-written ones.
NOTE_VERSION = 3
NOTE_PROVENANCE_NAMESPACE = "zs"
NOTE_PROVENANCE_SOURCE = "feed-batch"

_PRIORITY_GLYPH = {
    "must_read": "🔥",
    "should_read": "👀",
    "could_read": "📎",
    "dont_read": "—",
}


def build_provenance_comment(
    *,
    run_id: str | None = None,
    source: str = NOTE_PROVENANCE_SOURCE,
    version: int = NOTE_VERSION,
) -> str:
    """Build the HTML comment that marks a note as agent-generated.

    Parseable as ``key=value;key=value;...`` for any future tool that wants to
    grep notes by run_id, model, or version.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_run = (run_id or "").replace("-->", "").replace("<!--", "")
    safe_source = source.replace("-->", "").replace("<!--", "")
    fields = [
        f"{NOTE_PROVENANCE_NAMESPACE}:note_type=triage",
        f"version={int(version)}",
        f"generated_at={ts}",
        f"source={safe_source}",
    ]
    if safe_run:
        fields.append(f"run_id={safe_run}")
    return f"<!-- {';'.join(fields)} -->"


def build_triage_note_html(
    title: str,
    summary: SummarizeResponse,
    *,
    is_black_swan: bool = False,
    surprise_score: float | None = None,
    run_id: str | None = None,
    include_provenance: bool = True,
) -> str:
    """Render the persisted triage artifact as a self-sufficient Zotero note."""
    glyph = _PRIORITY_GLYPH.get(summary.reading_priority, "•")
    priority_label = summary.reading_priority.replace("_", " ").title()
    verdict = (summary.triage_rationale or summary.should_deep_read or summary.executive_summary or "").strip()
    if not verdict:
        verdict = f"Triaged paper: {title or 'Untitled'}."
    parts = [build_provenance_comment(run_id=run_id)] if include_provenance else []
    parts += [f"<h2>{html.escape(glyph)} {html.escape(priority_label)}</h2>",
              f"<p>{html.escape(verdict)}</p>"]
    text_sections = (
        ("What this paper is about", summary.executive_summary),
        ("Approach / methods", summary.methods),
        ("Why it matters to my work", summary.relevance_to_research),
        ("Limitations / uncertainty", summary.limitations),
        ("Reading guidance", summary.should_deep_read),
    )
    for heading, value in text_sections:
        if value and value.strip():
            parts += [f"<h2>{heading}</h2>", f"<p>{html.escape(value.strip())}</p>"]
    for heading, values, limit in (
        ("Key findings", summary.key_findings, 6),
        ("What to read", summary.key_sections_to_read, 6),
    ):
        kept = [str(value).strip() for value in values if str(value).strip()][:limit]
        if kept:
            parts += [f"<h2>{heading}</h2>", "<ul>" + "".join(
                f"<li>{html.escape(value)}</li>" for value in kept) + "</ul>"]
    tags_preview = ", ".join(html.escape(t) for t in (summary.tags or [])[:6]) or "—"
    footer_bits = [
        f"score {summary.composite_relevance_score:.1f}",
        f"goal: {html.escape(summary.matched_goal or '—')}",
        f"tags: {tags_preview}",
    ]
    if is_black_swan:
        if surprise_score is not None:
            footer_bits.append(f"🦢 surprise {surprise_score:.2f}")
        else:
            footer_bits.append("🦢 surprise pick")
    parts.append(f"<p><em>{' · '.join(footer_bits)}</em></p>")

    return "".join(parts)


# Marker for the single "your verdict" note on an item (upsert, no duplicates).
VERDICT_NOTE_MARKER = f"{NOTE_PROVENANCE_NAMESPACE}:note_type=verdict"


def build_verdict_note_html(user_priority: str, comment: str) -> str:
    """Render the short Zotero note for a user's reading verdict + comment."""
    glyph = _PRIORITY_GLYPH.get(user_priority, "•")
    label = (user_priority or "").replace("_", " ").title() or "Verdict"
    body = html.escape((comment or "").strip())
    return (
        f"<!-- {VERDICT_NOTE_MARKER};version=1 -->"
        f"<h2>{html.escape(glyph)} {html.escape(label)}</h2>"
        f"<p>{body}</p>"
    )


# Marker for the single free-text "my notes" note the user jots during a review
# (upsert, no duplicates). Distinct from the verdict note (a decision) — this is
# the user's own thinking, mirrored from the app's review_notes table.
USER_NOTE_MARKER = f"{NOTE_PROVENANCE_NAMESPACE}:note_type=user_note"


def build_user_note_html(note: str) -> str:
    """Render the user's free-text review note as one Zotero-safe note.

    Blank lines split paragraphs (each an escaped ``<p>``); a note with no body
    still renders the header so the marker survives an upsert that clears it.
    """
    paras = [html.escape(p.strip()) for p in (note or "").split("\n\n") if p.strip()]
    body = "".join(f"<p>{p}</p>" for p in paras) or "<p></p>"
    return f"<!-- {USER_NOTE_MARKER};version=1 --><h2>📝 My notes</h2>{body}"


# Marker for the single "deep digest" note on an item (upsert, no duplicates).
DIGEST_NOTE_MARKER = f"{NOTE_PROVENANCE_NAMESPACE}:note_type=digest"


def build_digest_note_html(digest: Any) -> str:
    """Render the condensed deep-review digest as one short Zotero note. Empty
    sections are skipped so it stays tight. Led by ``DIGEST_NOTE_MARKER``."""
    e = html.escape
    decision = (getattr(digest, "read_decision", "") or "—")
    minutes = getattr(digest, "estimated_read_minutes", None)
    timing = f" · {minutes} min" if minutes is not None else ""
    grade = getattr(digest, "grade", "") or "—"
    parts: list[str] = [
        f"<!-- {DIGEST_NOTE_MARKER};version=1 -->",
        f"<h2>Digest — {e(decision)}{timing} · Quality {e(grade)}</h2>",
    ]
    if getattr(digest, "tldr", ""):
        parts.append(f"<p>{e(digest.tldr)}</p>")
    if getattr(digest, "read_why", ""):
        parts.append(f"<p><strong>Read?</strong> {e(decision)} — {e(digest.read_why)}</p>")
    if getattr(digest, "original_value", ""):
        parts.append(f"<p><strong>What the original adds:</strong> {e(digest.original_value)}</p>")
    writing_reasons = list(getattr(digest, "writing_reasons", []) or [])
    if writing_reasons:
        parts.append(f"<p><strong>Writing · {e(digest.writing_friction)}:</strong> {e('; '.join(writing_reasons))}</p>")
    parameters = getattr(digest, "parameters", None)
    if parameters:
        values = [parameters.dataset, parameters.sample_size, parameters.architecture,
                  *parameters.baselines, *parameters.metrics]
        parts.append(f"<p><strong>Technical parameters:</strong> {e('; '.join(x for x in values if x))}</p>")
    read_parts = list(getattr(digest, "read_parts", []) or [])[:3]
    if read_parts:
        parts.append("<p><strong>Read parts</strong></p><ul>"
                     + "".join(f"<li>{e(str(x))}</li>" for x in read_parts) + "</ul>")
    skip_parts = list(getattr(digest, "skip_parts", []) or [])[:3]
    if skip_parts:
        parts.append("<p><strong>Skip parts</strong></p><ul>"
                     + "".join(f"<li>{e(str(x))}</li>" for x in skip_parts) + "</ul>")
    for label, val in (
        ("Relevance", getattr(digest, "relevance", "")),
        ("Controversies", getattr(digest, "controversies", "")),
        ("Impact", getattr(digest, "impact", "")),
        ("Unknown unknowns", getattr(digest, "unknown_unknowns", "")),
    ):
        if val:
            parts.append(f"<p><strong>{label}:</strong> {e(val)}</p>")
    impl = list(getattr(digest, "implementation", []) or [])[:3]
    if impl:
        parts.append("<p><strong>Implementation</strong></p><ul>"
                     + "".join(f"<li>{e(str(x))}</li>" for x in impl) + "</ul>")
    qline = (
        f"quality {e(grade)} · "
        f"sound {digest.soundness} · nov {digest.novelty} · sig {digest.significance} · "
        f"repro {digest.reproducibility} · clarity {digest.clarity}"
    )
    parts.append(f"<p><em>{qline}</em></p>")
    if getattr(digest, "key_strength", ""):
        parts.append(f"<p><em>+ {e(digest.key_strength)}</em></p>")
    if getattr(digest, "key_weakness", ""):
        parts.append(f"<p><em>− {e(digest.key_weakness)}</em></p>")
    return "".join(parts)
