"""Digest note builder: the WHOLE review as one scannable Zotero note.

Renders every populated field of the stored review entry in a fixed order
(verdict → TL;DR → findings → method → limits → impact → implementation →
reading guide → quality → paper type → goals → code → sections → flags).
Empty fields are omitted; every value is HTML-escaped (model text is untrusted).
Provider/model names, timings and audit internals are never rendered.
"""
from __future__ import annotations

from html import escape as e
from typing import Any, Iterable

# Marker/version are unchanged so an upsert replaces the previous digest note.
DIGEST_NOTE_MARKER = "zs:note_type=digest"


def _g(obj: Any, name: str, default: Any = "") -> Any:
    return getattr(obj, name, default) or default


def _p(label: str, value: Any) -> str:
    return f"<p><strong>{label}:</strong> {e(str(value))}</p>" if value else ""


def _ul(label: str, items: Iterable[Any]) -> str:
    rows = [str(x) for x in items if str(x).strip()]
    if not rows:
        return ""
    return f"<p><strong>{label}</strong></p><ul>" + "".join(f"<li>{e(x)}</li>" for x in rows) + "</ul>"


def _parameters(digest: Any) -> str:
    p = _g(digest, "parameters", None)
    if not p:
        return ""
    rows = [("Dataset", p.dataset), ("Sample size", p.sample_size), ("Architecture", p.architecture),
            ("Baselines", "; ".join(p.baselines)), ("Metrics", "; ".join(p.metrics))]
    return _ul("Technical parameters", (f"{k}: {v}" for k, v in rows if v))


def _verification(entry: dict[str, Any]) -> str:
    audit = entry.get("verification_audit")
    if not isinstance(audit, dict) or audit.get("status") != "accepted_verifier_evidence":
        return "not verified"
    changed = sorted({*(audit.get("post_verification_changes") or []), *(entry.get("not_established_fields") or [])})
    return (f"partially grounded ({len(changed)} fields not established)" if changed else "passed")


def _checks(q: dict[str, Any]) -> str:
    n = q.get("coverage_applicable") or 0
    return f"{q.get('coverage_met', 0)} of {n} checks" if n else ""


def _quality(q: dict[str, Any], verification: str) -> str:
    if not q:
        return f"<h3>Quality</h3>{_p('Verification', verification)}"
    head = [f"Grade {q['grade']}" if q.get("grade") else "", f"band {q['quality_band']}" if q.get("quality_band") else "",
            _checks(q)]
    if q.get("passes_total") == 1:
        head.append("1 pass")
    elif q.get("passes_total"):
        head.append(f"{q.get('passes_agreed', 0)}/{q['passes_total']} passes agree")
    if q.get("type_uncertain"):
        head.append("type uncertain")
    evidence = q.get("evidence") or {}
    rubric = [f"{name}: {mark}" + (f" — “{evidence[name]}”" if evidence.get(name) else "")
              for name, mark in (q.get("rubric") or {}).items()]
    grounding = q.get("claim_grounding_rate")
    return "".join([
        "<h3>Quality</h3>", _p("Summary", " · ".join(x for x in head if x)),
        _p("Standard", q.get("coverage_standard")), _p("Domain", q.get("domain")),
        _ul("Checks", rubric), _ul("Missing critical", q.get("missing_critical") or []),
        _ul("Red flags", q.get("red_flags") or []), _ul("Possible overstatement (low reliability)", q.get("overstatements") or []),
        _ul("Demoted by self-verification", q.get("self_verification_demoted") or []),
        _p("Claim grounding", f"{grounding:.0%}") if grounding else "", _p("Verification", verification),
    ])


def _paper_type(pt: dict[str, Any]) -> str:
    if not pt or not pt.get("type"):
        return ""
    kind = f"{pt['type']} (confidence {pt['confidence']:.2f})" if pt.get("confidence") else pt["type"]
    return "".join([_p("Paper type", kind), _p("Secondary type", pt.get("secondary")),
                    _p("Why this type", pt.get("reasoning"))])


def _goals(goals: list[dict[str, Any]]) -> str:
    out = []
    for g in goals or []:
        state = "relevant" if g.get("relevant") else "not relevant"
        quotes = g.get("supporting_quotes") or []
        out.append(f"<li><strong>{e(g.get('goal', ''))}</strong> — {state}"
                   + (f"<br/>{e(g['summary'])}" if g.get("summary") else "")
                   + (f"<br/>Sections: {e('; '.join(g['key_sections']))}" if g.get("key_sections") else "")
                   + ("<ul>" + "".join(f"<li>“{e(q)}”</li>" for q in quotes) + "</ul>" if quotes else "")
                   + "</li>")
    return "<h3>Goal match</h3><ul>" + "".join(out) + "</ul>" if out else ""


def _code(link: dict[str, Any]) -> str:
    if not link or not link.get("found") or not str(link.get("url") or "").lower().startswith(("http://", "https://")):
        return ""
    url = e(link["url"], quote=True)
    note = f" — {e(link['description'])}" if link.get("description") else ""
    return f'<p><strong>Code / data:</strong> <a href="{url}">{e(link["url"])}</a>{note}</p>'


def _sections(overlay: dict[str, Any]) -> str:
    rows = [f"{s.get('title', '')}: {s['summary']}" for s in (overlay or {}).get("sections", []) if s.get("summary")]
    return _ul("Sections", rows)


def _body(digest: Any, decision: str) -> list[str]:
    writing = list(_g(digest, "writing_reasons", []))
    friction = _g(digest, "writing_friction")
    return [
        _p("Verdict", _g(digest, "verdict")),
        f"<p>{e(digest.tldr)}</p>" if _g(digest, "tldr") else "",
        _p("Executive summary", _g(digest, "executive_summary")),
        _ul("Key findings", _g(digest, "key_findings", [])),
        _p("Read?", f"{decision} — {digest.read_why}" if _g(digest, "read_why") else ""),
        _p("What the original adds", _g(digest, "original_value")),
        _p("Methods", _g(digest, "methods")), _parameters(digest),
        _p("Limitations", _g(digest, "limitations")), _p("Controversies", _g(digest, "controversies")),
        _p("Relevance", _g(digest, "relevance")), _p("Impact", _g(digest, "impact")),
        _p("Industry impact", _g(digest, "industry_impact")), _p("Academy impact", _g(digest, "academy_impact")),
        _p("Unknown unknowns", _g(digest, "unknown_unknowns")),
        _ul("Implementation", _g(digest, "implementation", [])),
        _ul("Read parts", _g(digest, "read_parts", [])), _ul("Skip parts", _g(digest, "skip_parts", [])),
        _p(f"Writing · {friction}", "; ".join(writing)) if writing else "",
        f"<p><em>+ {e(digest.key_strength)}</em></p>" if _g(digest, "key_strength") else "",
        f"<p><em>− {e(digest.key_weakness)}</em></p>" if _g(digest, "key_weakness") else "",
    ]


def build_digest_note_html(digest: Any, *, entry: dict[str, Any] | None = None,
                           interpretation: dict[str, Any] | None = None) -> str:
    """Render the digest (digest-only path) or the whole stored review ``entry``.

    ``entry`` adds quality, paper type, goal board, code link, section one-liners
    and policy flags. Performs no writes."""
    entry = entry or {}
    decision = _g(digest, "read_decision", "—")
    quality = entry.get("quality") or {}
    minutes = _g(digest, "estimated_read_minutes", None)
    grade = _g(digest, "grade", "")
    header = " · ".join(x for x in [e(decision), f"{minutes} min" if minutes else "",
                                    f"Quality {e(grade)}" if grade else "",
                                    f"band {e(quality['quality_band'])}" if quality.get("quality_band") else "",
                                    e(_checks(quality))] if x)
    parts = [f"<!-- {DIGEST_NOTE_MARKER};version=1 -->", f"<h2>Digest — {header}</h2>", *_body(digest, decision)]
    if entry:
        parts += [_quality(quality, _verification(entry)), _paper_type(entry.get("paper_type") or {}),
                  _goals(entry.get("goal_summaries") or []), _code(entry.get("code_link") or {}),
                  _sections(entry.get("section_overlay") or {}),
                  _ul("Reading policy flags", entry.get("reading_policy_flags") or [])]
    if interpretation is not None:
        from zotero_summarizer.services.library._interpretation_html import section
        parts.append(section(interpretation, digest))
    scores = (f"sound {digest.soundness} · nov {digest.novelty} · sig {digest.significance} · "
              f"repro {digest.reproducibility} · clarity {digest.clarity}")
    parts.append(f"<p><em>quality {e(grade)} · {scores} · {DIGEST_NOTE_MARKER};version=1</em></p>")
    return "".join(parts)
