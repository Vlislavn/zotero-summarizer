"""Find and validate the paper's OWN code repository from its full text.

A deep-review enrichment layer. Papers that release code almost always print a
"Code is available at https://github.com/owner/repo" line, so we pull every
github link, prefer the one sitting next to an availability phrase (so a cited
dependency / baseline repo doesn't outrank the paper's OWN repo), confirm it
resolves, and judge whether it plausibly belongs to THIS paper (availability
context OR the repo description echoing words from the title). The result is
cached on the deep-review entry as ``code_link`` and rendered at the top of the
brief.

Extraction/ranking is pure here; the network probe lives in the integrations
leaf ``integrations.github`` (layering: services → integrations).
"""
from __future__ import annotations

import re
from typing import Any

from zotero_summarizer.integrations import github
from zotero_summarizer.settings import offline_requested

# github.com/{owner}/{repo}. The repo segment stops before a path/anchor so
# ".../repo/blob/main/x" → "repo"; a trailing "." / ")" from prose is dropped by
# the char-class boundary or _normalize_repo.
_GITHUB_RE = re.compile(
    r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/([A-Za-z0-9_.-]+)", re.I
)
# github.com/<x> paths that are site features, not user repos.
_RESERVED_OWNERS = frozenset({
    "features", "about", "pricing", "sponsors", "topics", "collections",
    "marketplace", "apps", "settings", "orgs", "explore", "trending",
    "notifications", "new", "login", "join", "search", "site", "readme",
})
# An availability phrase near a URL = the link is CLAIMED as the project's code.
_AVAILABILITY_RE = re.compile(
    r"(?:code|implementation|software|source|models?|weights?|dataset|repositor)\w*"
    r"[^.]{0,40}?(?:available|released?|public|accessible|can be found|provided|hosted|at)\b"
    r"|we (?:release|provide|made?|open[- ]?source)"
    r"|(?:our|the) (?:code|implementation|repositor)",
    re.I,
)
_AVAILABILITY_WINDOW = 200  # chars before a URL scanned for an availability phrase
_MAX_CHECKS = 3             # cap live probes per paper (the ranked top-K)
_TITLE_OVERLAP_MIN = 2      # significant title tokens the repo must echo to "match"
# Word-overlap stopwords: prose glue + github-page boilerplate (og:description is
# "{desc}. Contribute to owner/repo development by creating an account on GitHub.")
# + framework names that would false-match any ML paper.
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with", "via",
    "using", "from", "by", "is", "are", "we", "our", "this", "that", "based", "toward",
    "github", "contribute", "creating", "account", "development", "repository", "repo",
    "code", "implementation", "official", "pytorch", "tensorflow", "jax", "paper",
})


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def _normalize_repo(repo: str) -> str:
    repo = repo.strip().rstrip(").,;:'\"")
    return repo[:-4] if repo.lower().endswith(".git") else repo


def extract_candidates(text: str) -> list[dict[str, Any]]:
    """Distinct ``github.com/owner/repo`` links in order, each tagged with whether
    an availability phrase sits just before it. Availability-near links rank first
    (the paper's OWN repo beats a cited dependency); ties keep first-seen order."""
    seen: dict[str, dict[str, Any]] = {}
    for m in _GITHUB_RE.finditer(text or ""):
        owner, repo = m.group(1), _normalize_repo(m.group(2))
        if not repo or owner.lower() in _RESERVED_OWNERS:
            continue
        key = f"{owner}/{repo}".lower()
        if key in seen:
            continue
        window = text[max(0, m.start() - _AVAILABILITY_WINDOW): m.start()]
        seen[key] = {
            "owner": owner, "repo": repo,
            "url": f"https://github.com/{owner}/{repo}",
            "near_availability": bool(_AVAILABILITY_RE.search(window)),
        }
    cands = list(seen.values())
    cands.sort(key=lambda c: 0 if c["near_availability"] else 1)  # stable: near first
    return cands


# Live > unknown > dead, so a renamed/private 404 never wins over a working repo.
_EXISTS_RANK = {True: 2, None: 1, False: 0}


def find_code_link(
    text: str, title: str = "", *, offline: bool | None = None, checker=github.check_repo,
) -> dict[str, Any]:
    """The cached ``code_link`` verdict for a paper: ``{"found": False}`` when no
    github repo is mentioned, else the best candidate with liveness + relevance.

    ``relevance`` is ``matched`` when the link is availability-claimed OR the repo
    description echoes ≥``_TITLE_OVERLAP_MIN`` significant title words, else
    ``unverified``. ``checker`` is injectable so tests run without network.

    # ponytail: relevance is a 2-signal heuristic (context + title overlap). Upgrade
    # path if it proves weak: owner==author / README-cites-paper via the github API.
    """
    cands = extract_candidates(text)
    if not cands:
        return {"found": False}
    if offline is None:
        offline = offline_requested()
    top = cands[0]
    if offline:
        return {"found": True, "url": top["url"], "exists": None, "checked": False,
                "relevance": "unverified", "near_availability": top["near_availability"]}

    title_tokens = _tokens(title)
    probed: list[dict[str, Any]] = []
    for cand in cands[:_MAX_CHECKS]:
        res = checker(cand["owner"], cand["repo"])
        overlap = len(title_tokens & _tokens(f"{res.description} {res.title} {cand['repo']}"))
        matched = cand["near_availability"] or overlap >= _TITLE_OVERLAP_MIN
        entry = {
            "found": True, "url": cand["url"], "exists": res.exists, "status": res.status,
            "checked": True, "near_availability": cand["near_availability"],
            "title_overlap": overlap, "description": res.description,
            "relevance": "matched" if matched else "unverified",
        }
        if res.exists and matched:
            return entry  # a live repo that's clearly this paper's — done.
        probed.append(entry)
    probed.sort(key=lambda e: (_EXISTS_RANK[e["exists"]], e["relevance"] == "matched"), reverse=True)
    return probed[0]


def _demo() -> None:
    """Self-check: extraction, reserved-owner exclusion, availability ranking,
    relevance via title overlap, and the offline / no-repo contracts. No network."""
    text = (
        "We evaluate baselines from https://github.com/facebookresearch/detectron2. "
        "Our code is available at https://github.com/acme/wonder-net for reproduction. "
        "CI runs on github.com/features/actions."
    )
    keys = [f"{c['owner']}/{c['repo']}" for c in extract_candidates(text)]
    assert "facebookresearch/detectron2" in keys, keys
    assert "acme/wonder-net" in keys, keys
    assert "features/actions" not in keys, "reserved owner not excluded"
    assert extract_candidates(text)[0]["owner"] == "acme", "availability-near must rank first"

    def fake(owner: str, repo: str) -> github.RepoCheck:
        if owner == "acme":
            return github.RepoCheck(True, 200, "WonderNet: a wonder network for triage", "acme/wonder-net")
        return github.RepoCheck(True, 200, "Detectron2 object detection", "")

    res = find_code_link(text, "WonderNet wonder network", offline=False, checker=fake)
    assert res["found"] and res["url"].endswith("acme/wonder-net"), res
    assert res["relevance"] == "matched", res

    # overlap-only path (no availability phrase) still matches via the title.
    plain = "Experiments use https://github.com/acme/wonder-net heavily."
    res2 = find_code_link(plain, "WonderNet wonder network", offline=False, checker=fake)
    assert res2["relevance"] == "matched" and res2["title_overlap"] >= 2, res2

    # a dead repo never wins over a live one.
    def fake_dead_first(owner: str, repo: str) -> github.RepoCheck:
        return github.RepoCheck(False, 404, "", "") if owner == "acme" else github.RepoCheck(True, 200, "x", "")
    text3 = ("Our code is available at https://github.com/acme/wonder-net . "
             "Baseline at https://github.com/other/live-repo .")
    res3 = find_code_link(text3, "unrelated", offline=False, checker=fake_dead_first)
    assert res3["exists"] is True and res3["url"].endswith("other/live-repo"), res3

    assert find_code_link("no links here", "x")["found"] is False
    off = find_code_link(text, "x", offline=True)
    assert off["exists"] is None and off["checked"] is False, off
    print("ok")


__all__ = ["extract_candidates", "find_code_link"]


if __name__ == "__main__":
    _demo()
