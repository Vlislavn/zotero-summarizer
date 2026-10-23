"""Pure, explicit consumption of one trusted current-review/view packet.

No cache read, qualification, passage reconstruction or scientific approval.
"""
from __future__ import annotations

import html
import json
from typing import Any

from zotero_summarizer.models.interpretation import interpretation_references

_GROUP_FIELDS = {
    "Finding": ("finding_ref", "inference_strength", "rationale"),
    "Scope": ("finding_ref", "scope", "basis_refs"),
    "Unproven": ("finding_ref", "uncertainties", "unsupported_extrapolations", "basis_refs"),
}
_DISCLOSURES = {
    "origin": "ai_generated", "scientific_verification": "unverified",
    "contradiction_adjudication": "not_performed", "evidence_state": "bound_snapshot",
}


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Expected a whole qualified interpretation review/view packet")


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _packet_view(review: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(review, dict) and isinstance(review.get("digest"), dict) and bool(review["digest"]))
    _require(isinstance(review.get("review_identity"), dict) and bool(review["review_identity"]))
    view, qualification = review["interpretation_view"], review["interpretation_qualification"]
    _require(isinstance(view, dict) and isinstance(qualification, dict))
    _require(set(view) == {"availability", "reason", "groups", "passage_support", *_DISCLOSURES})
    _require(all(view[key] == value for key, value in _DISCLOSURES.items()))
    _require(view["availability"] in {"available", "unavailable"} and isinstance(view["reason"], str))
    _require(qualification["status"] == view["availability"] and qualification["reason"] == view["reason"])
    _require(qualification["audit_status"] == "available" and qualification["audit_reason"] == "")
    _require(isinstance(view["groups"], dict) and set(view["groups"]) == set(_GROUP_FIELDS))
    _require(all(isinstance(rows, list) for rows in view["groups"].values()))
    _require(isinstance(view["passage_support"], list))
    for key, kind in (("quality", dict), ("goal_summaries", list), ("code_link", dict)):
        _require(review.get(key) is None or isinstance(review[key], kind))
    if view["availability"] == "unavailable":
        _require(bool(view["reason"]) and not any(view["groups"].values()) and not view["passage_support"])
        _require("interpretation" not in review and "verification_audit" not in review)
    else:
        _require(view["reason"] == "" and bool(view["groups"]["Finding"]) and bool(view["passage_support"]))
        _require(isinstance(review["interpretation"], dict) and isinstance(review["verification_audit"], dict))
    return view


def _validate_groups(groups: dict, references: dict) -> None:
    count = len(groups["Finding"])
    _require(all(len(rows) == count for rows in groups.values()))
    for name, fields in _GROUP_FIELDS.items():
        for index, row in enumerate(groups[name]):
            _require(isinstance(row, dict) and set(row) == set(fields))
            ref = row["finding_ref"]
            _require(isinstance(ref, str) and ref in references)
            _require(ref == "/tldr" or ref.startswith("/key_findings/"))
            _require(ref == groups["Finding"][index]["finding_ref"])
            for key in fields[1:]:
                _require(_strings(row[key]) if key in {"basis_refs", "uncertainties", "unsupported_extrapolations"}
                         else isinstance(row[key], str))
            if "basis_refs" in row:
                _require(all(ref in references and ref != "/tldr" and not ref.startswith("/key_findings/")
                             for ref in row["basis_refs"]))
    _require(all(scope["basis_refs"] == unproven["basis_refs"]
                 for scope, unproven in zip(groups["Scope"], groups["Unproven"])))


def _validate_support(rows: list) -> None:
    previous = -1
    for row in rows:
        _require(isinstance(row, dict) and set(row) == {
            "claim_index", "scientific_ref", "part", "availability", "reason", "passage_refs",
        })
        _require(type(row["claim_index"]) is int and row["claim_index"] > previous)
        _require(type(row["part"]) is int and row["part"] >= 0 and isinstance(row["scientific_ref"], str))
        _require(row["availability"] == "available" and row["reason"] == "")
        _require(isinstance(row["passage_refs"], list) and bool(row["passage_refs"]))
        _require(all(type(ref) is int and ref >= 0 for ref in row["passage_refs"]))
        previous = row["claim_index"]


def _h(value: str) -> str:
    return html.escape(value, quote=True)


def _value(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    return f'<div class="interpretation-value">{_h(text)}</div>'


def _row(label: str, value: Any) -> str:
    return f'<div class="drow"><dt class="dlbl">{_h(label)}</dt><dd>{_value(value)}</dd></div>'


def _list(label: str, values: list[str]) -> str:
    body = '<ol class="dbullets">' + "".join(f'<li>{_value(value)}</li>' for value in values) + '</ol>'
    if not values:
        body = '<span>No entries recorded; this is not proof of absence.</span>'
    return f'<div class="drow"><dt class="dlbl">{_h(label)}</dt><dd>{body}</dd></div>'


def _basis(refs: list[str], references: dict) -> str:
    body = "".join(f'<li><code>{_h(ref)}</code>{_value(references[ref])}</li>' for ref in refs)
    return ('<div class="drow"><dt class="dlbl">Referenced basis</dt><dd><ol class="dbullets">' + body +
            '</ol></dd></div>' if refs else _row("Referenced basis", "No basis references recorded."))


def _group(name: str, rows: list[dict], references: dict) -> str:
    items = []
    for index, row in enumerate(rows, 1):
        ref = row["finding_ref"]
        body = _row("Finding reference", ref)
        if name == "Finding":
            body += _row("Referenced finding", references[ref])
            body += _row("Inference strength", row["inference_strength"]) + _row("Rationale", row["rationale"])
        elif name == "Scope":
            body += _row("Scope", row["scope"]) + _basis(row["basis_refs"], references)
        else:
            body += _list("Uncertainties", row["uncertainties"])
            body += _list("Unsupported extrapolations", row["unsupported_extrapolations"])
            body += _list("Basis references (values in Scope)", row["basis_refs"])
        items.append(f'<li><p>Finding {index}</p><dl>{body}</dl></li>')
    return f'<div class="interpretation-group"><h3>{name}</h3><ol>{"".join(items)}</ol></div>'


def _support(rows: list[dict]) -> str:
    items = []
    for row in rows:
        body = _row("Claim index", row["claim_index"]) + _row("Scientific reference", row["scientific_ref"])
        body += _row("Sentence part", row["part"]) + _row("Passage IDs", row["passage_refs"])
        items.append(f'<li><dl>{body}</dl></li>')
    return ('<details class="interpretation-support"><summary>Native support references</summary>'
            '<p>Passage IDs belong to the accompanying review snapshot. Original passages, pages and '
            'working paper anchors are not embedded here.</p><ol>' + "".join(items) + '</ol></details>')


def render_review(review: dict[str, Any]) -> str:
    """Render the supplied view; trust its caller, never reopen or requalify it."""
    view = _packet_view(review)
    references = interpretation_references(review["digest"])
    _validate_groups(view["groups"], references)
    _validate_support(view["passage_support"])
    body = ('<p>AI-generated · scientifically unverified · bound snapshot.</p>'
            '<p>Contradiction adjudication: not performed. Native support is not scientific, human or author '
            'approval, causal warrant or full-paper coverage. This renderer does not authenticate the packet '
            'or guarantee that it is still current.</p>')
    if view["availability"] == "unavailable":
        body += '<dl>' + _row("Interpretation unavailable", view["reason"]) + '</dl>'
        body += '<p>The base review remains available; optional interpretation and its support are withheld.</p>'
    else:
        body += "".join(_group(name, view["groups"][name], references) for name in _GROUP_FIELDS)
        body += _support(view["passage_support"])
    return ('<section id="interpretation" class="interpretation-review" aria-labelledby="interpretation-heading">'
            '<h2 id="interpretation-heading">Finding / Scope / Unproven</h2>' + body + '</section>')


def review_css() -> str:
    """Scoped styling emitted only for explicit packet consumption."""
    return """
.interpretation-review{margin:24px 0;padding:20px;border:1px solid var(--border);border-radius:14px;background:var(--card);overflow-wrap:anywhere;font-weight:400}
.interpretation-review h2,.interpretation-review h3{color:var(--hema);font-family:var(--font-display);font-weight:500;line-height:1.4}
.interpretation-review h2{font-size:20px;margin:0 0 12px}.interpretation-review h3{font-size:17px;margin:0 0 12px}
.interpretation-review p{margin:8px 0}.interpretation-review .interpretation-group,.interpretation-review .interpretation-support{border-top:1px solid var(--hair);margin-top:18px;padding-top:16px}
.interpretation-review .interpretation-support>summary{cursor:pointer;color:var(--hema);font-family:var(--font-display);font-weight:500;line-height:1.4;font-size:17px}
.interpretation-review .interpretation-support>summary:focus-visible{outline:2px solid var(--hema);outline-offset:3px}
.interpretation-review ol{padding-left:22px}.interpretation-review li{margin:12px 0}.interpretation-review dl{margin:0;display:grid;gap:10px}
.interpretation-review dd{margin:0;min-width:0}.interpretation-review code{white-space:pre-wrap;color:var(--muted);font:inherit}
.interpretation-value{white-space:pre-wrap;overflow-wrap:anywhere;font-size:15px}
@media(max-width:600px){.interpretation-review{padding:14px}.interpretation-review .drow{grid-template-columns:1fr}}
@media print{.interpretation-review details.interpretation-support::details-content{content-visibility:visible}}
"""
