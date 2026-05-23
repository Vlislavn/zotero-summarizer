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
    """Render a concise, Zotero-safe triage note: verdict + key findings +
    relevance + a compact metadata footer (target <150 words rendered)."""
    glyph = _PRIORITY_GLYPH.get(summary.reading_priority, "•")
    priority_label = summary.reading_priority.replace("_", " ").title()

    verdict = (summary.triage_rationale or summary.should_deep_read or summary.executive_summary or "").strip()
    if not verdict:
        verdict = f"Triaged paper: {title or 'Untitled'}."

    findings = [f for f in (summary.key_findings or []) if str(f).strip()][:3]
    findings_html = "".join(f"<li>{html.escape(str(f))}</li>" for f in findings) or "<li><em>No specific findings extracted.</em></li>"

    relevance = (summary.relevance_to_research or "").strip()
    if not relevance:
        relevance = "(No specific connection to your goals extracted.)"

    tags_preview = ", ".join(html.escape(t) for t in (summary.tags or [])[:3]) or "—"
    matched_goal = html.escape(summary.matched_goal or "—")

    parts: list[str] = []
    if include_provenance:
        parts.append(build_provenance_comment(run_id=run_id))
    parts.extend(
        [
            f"<h2>{html.escape(glyph)} {html.escape(priority_label)}</h2>",
            f"<p>{html.escape(verdict)}</p>",
            "<h2>Key findings</h2>",
            f"<ul>{findings_html}</ul>",
            "<h2>Relevance to my work</h2>",
            f"<p>{html.escape(relevance)}</p>",
        ]
    )

    footer_bits = [
        f"score {summary.composite_relevance_score:.1f}",
        f"goal: {matched_goal}",
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


# Marker for the single "deep digest" note on an item (upsert, no duplicates).
DIGEST_NOTE_MARKER = f"{NOTE_PROVENANCE_NAMESPACE}:note_type=digest"


def build_digest_note_html(digest: Any) -> str:
    """Render the condensed deep-review digest as one short Zotero note. Empty
    sections are skipped so it stays tight. Led by ``DIGEST_NOTE_MARKER``."""
    e = html.escape
    decision = (getattr(digest, "read_decision", "") or "—")
    grade = getattr(digest, "grade", "") or "—"
    parts: list[str] = [
        f"<!-- {DIGEST_NOTE_MARKER};version=1 -->",
        f"<h2>Digest — {e(decision)} · Quality {e(grade)}</h2>",
    ]
    if getattr(digest, "tldr", ""):
        parts.append(f"<p>{e(digest.tldr)}</p>")
    if getattr(digest, "read_why", ""):
        parts.append(f"<p><strong>Read?</strong> {e(decision)} — {e(digest.read_why)}</p>")
    read_parts = list(getattr(digest, "read_parts", []) or [])[:3]
    if read_parts:
        parts.append("<p><strong>Read parts</strong></p><ul>"
                     + "".join(f"<li>{e(str(x))}</li>" for x in read_parts) + "</ul>")
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
