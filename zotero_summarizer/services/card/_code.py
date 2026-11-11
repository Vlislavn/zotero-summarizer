"""Code-availability fact: the paper's OWN live repository, if any.

Upgrades the plain :func:`services.library._code_link.find_code_link` link
extraction (reused for candidate harvesting, fed the PDF hyperlink annotations)
with the ownership cascade ported from ``data/perf-20261107/card/v3/det_v3.own_code``:
a github link counts as THIS paper's own code only when it appears OUTSIDE the
reference list with own-release wording ("our code … available at"), or in the
abstract/intro block (not a citation), or its slug matches the paper's method name.
A cited dependency / baseline's released implementation is therefore NOT reported
as the paper's own code (the pilot third-party guard). Liveness is the keyless
``integrations.github.check_repo`` probe.

``status``: ``own_live`` (his code, reachable) · ``promised`` (claimed own, repo
404/410) · ``own_unverified`` (claimed own, liveness unknown/offline) · ``unclear``
(a repo is mentioned but ownership is not established) · ``none`` (full text read, no
repo) · ``unknown`` (no full text obtained — nothing was scanned, absence unproven).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from zotero_summarizer.integrations import github
from zotero_summarizer.services.library import _code_link
from zotero_summarizer.settings import offline_requested

if TYPE_CHECKING:
    from zotero_summarizer.services.card._fulltext import FullText

_REF_HEAD = re.compile(r"(?im)^\s*(references|bibliography|literature cited)\s*$")
_GITHUB_SLUG = re.compile(r"github\.com/([\w.\-]+/[\w.\-]+)", re.I)
# Own-release wording near the link (det_v3._OWN_CTX).
_OWN_CTX = re.compile(
    r"\b(our|we|the authors'?)\b[^.\n]{0,40}\b(code|implementation|source|repository|repo|framework"
    r"|library|benchmark|dataset|data|release|toolkit|platform|artifacts?)\b"
    r"|\bwe (release|open[- ]?source|publish|provide|make [^.]{0,40}(available|public))\b"
    r"|\b(code|data|implementation|repository|project page|resources|benchmark|models?)\b[^.\n]{0,50}"
    r"\b(is|are|will be)?\s*(publicly |openly )?(available|released|hosted)\s*(at|on|here|in|from|under)?\b"
    r"|\b(available|released|hosted|found)\s+(at|on|here)\b"
    r"|\b(code|data|github|project page|repository|resources|demo)\s*[:\uff1a]|\bopen-?sourced?\b", re.I)
_CITE_CTX = re.compile(
    r"\bet al\.?|\[\d+(?:[,\u2013-]\s*\d+)*\]|\(\w+,? \d{4}\)"
    r"|\b(retrieved|accessed|in proceedings|arXiv preprint|preprint arXiv|url|ieee|acm)\b", re.I)
_GENERIC_NAME = {
    "agent", "agents", "code", "paper", "research", "benchmark", "bench", "project",
    "open", "llm", "ai", "model", "models", "system", "framework",
}


def _name_tokens(title: str) -> list[str]:
    """The paper's method/name tokens: the part before ':' (if short) + CamelCase/
    ALLCAPS title words (det_v3._name_tokens)."""
    toks: list[str] = []
    head = (title or "").split(":")[0].strip() if ":" in (title or "") else ""
    if head and len(head.split()) <= 3:
        toks.append(head)
    toks += re.findall(r"\b[A-Z][a-z]+[A-Z]\w*\b|\b[A-Z]{3,}\w*\b", title or "")
    return [t for t in (re.sub(r"[^a-z0-9]", "", x.casefold()) for x in toks) if len(t) >= 4]


def _title_tokens(title: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", (title or "").casefold()))


def _own_code(text: str, annotations: str, title: str, url: str) -> dict[str, Any]:
    """Is ``url`` presented as THIS paper's own repo? (det_v3.own_code + the
    code_chain slug↔title rule, ported.) Distinctive-name signals also accept a
    link that exists only as a front-matter PDF hyperlink annotation."""
    match = _GITHUB_SLUG.search(url or "")
    if not match or not text:
        return {"own": False, "reason": "no github slug"}
    slug = match.group(1).rstrip(".").casefold()
    heads = list(_REF_HEAD.finditer(text))
    refs_at = heads[-1].start() if heads and heads[-1].start() > 0.3 * len(text) else len(text) + 1
    body = [m.start() for m in re.finditer(re.escape(slug), text, re.I) if m.start() < refs_at]
    in_annot = bool(re.search(re.escape(slug), annotations or "", re.I))
    # Strong prose signals (need an in-body occurrence with context).
    for p in body:
        pre = text[max(0, p - 260): p]
        if _OWN_CTX.search(pre + " " + text[p: p + 60]) and not _CITE_CTX.search(text[max(0, p - 90): p + 60]):
            return {"own": True, "reason": "own-code wording near the link"}
    for p in body:
        if p < 12000 and not _CITE_CTX.search(text[max(0, p - 90): p + 60]):
            return {"own": True, "reason": "link in the abstract/intro block (first 12k chars), not a citation"}
    # Distinctive-name signals: a repo named after the paper's method is theirs even
    # when the link is only a front-matter annotation. Safe because the name must be
    # the paper's own CamelCase/colon-head method name or its multi-word title slug.
    if body or in_annot:
        repo = slug.split("/")[1]
        name = re.sub(r"[^a-z0-9]", "", repo)
        for n in _name_tokens(title):
            if n not in _GENERIC_NAME and (n in name or (len(name) >= 5 and name in n)):
                return {"own": True, "reason": f"repo name '{name}' matches method name '{n}'"}
        slug_tokens = set(re.findall(r"[a-z]{3,}", repo)) - _GENERIC_NAME
        if len(slug_tokens) >= 2 and slug_tokens <= _title_tokens(title):
            return {"own": True, "reason": "repo slug matches the paper title"}
    where = "only in the reference list" if (body or in_annot) is False and re.search(re.escape(slug), text, re.I) else (
        "ownership not established")
    return {"own": False, "reason": where}


def _live_status(owner: str, repo: str) -> tuple[str, bool | None, int | None]:
    res = github.check_repo(owner, repo)
    status = "own_live" if res.exists is True else "promised" if res.exists is False else "own_unverified"
    return status, res.exists, res.status


def code_facts(full: "FullText", title: str, *, offline: bool | None = None) -> dict[str, Any]:
    text = full.text or ""
    annotations = "\n".join(u for u in (full.link_uris or []) if "github.com" in u.lower())
    candidates = _code_link.extract_candidates(f"{text}\n{annotations}")
    if not candidates:
        # No full text means we never looked at the paper body: report ``unknown``
        # (not a proven absence) so the card shows «код: неизвестно», not «нет кода».
        status = "none" if full.pdf_path else "unknown"
        return {"found": False, "url": "", "status": status, "exists": None, "http": None, "own_reason": None}

    if offline is None:
        offline = offline_requested()
    owned = [(c, v["reason"]) for c in candidates
             if (v := _own_code(text, annotations, title, c["url"]))["own"]]
    if owned:
        cand, reason = owned[0]
        if offline:
            status, exists, http = "own_unverified", None, None
        else:
            status, exists, http = _live_status(cand["owner"], cand["repo"])
        return {"found": True, "url": cand["url"], "status": status, "exists": exists,
                "http": http, "own_reason": reason}

    top = candidates[0]  # a repo is mentioned but not established as the paper's own
    return {"found": True, "url": top["url"], "status": "unclear", "exists": None,
            "http": None, "own_reason": None}


__all__ = ["code_facts"]
