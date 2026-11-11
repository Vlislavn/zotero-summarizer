"""Who is behind the paper.

Affiliations are parsed deterministically from the PDF header (the block before
the abstract heading, with journal boilerplate stripped — the pilot rule). The
author h-index comes from OpenAlex via the app's configured ``OpenAlexClient``
(which is backed by the shared ``OpenAlexCache``); on any miss it fails soft.
"""
from __future__ import annotations

import re
from typing import Any

from zotero_summarizer.services._common import LOGGER, state

# Known research orgs the owner trusts as a credibility signal (pilot KNOWN list).
KNOWN_ORGS = (
    "Google", "DeepMind", "Microsoft", "Meta AI", "FAIR", "OpenAI", "Anthropic", "NVIDIA",
    "Amazon", "Apple", "IBM", "Allen Institute", "Mila", "Stanford", "MIT",
    "Massachusetts Institute of Technology", "Carnegie Mellon", "Berkeley", "Princeton",
    "Harvard", "Yale", "Oxford", "Cambridge", "ETH", "EPFL", "Tsinghua", "Peking University",
    "University of Toronto", "University of Washington", "Cornell", "Columbia University",
    "UIUC", "UCLA", "UC San Diego", "Georgia Tech", "University of Michigan",
    "New York University", "Imperial College", "University College London", "UCL", "Edinburgh",
    "Max Planck", "KAIST", "National University of Singapore", "Nanyang", "Mayo Clinic",
    "MD Anderson", "Broad Institute", "Inria", "CNRS", "Alibaba", "Tencent", "ByteDance",
    "Baidu", "Huawei", "Samsung", "Salesforce", "Adobe", "Mistral", "Cohere", "Hugging Face",
    "Johns Hopkins", "Duke University", "University of Pennsylvania", "Vector Institute",
    "Technical University of Munich", "Heidelberg", "Zhejiang University", "Shanghai Jiao Tong",
    "Fudan", "Chinese Academy of Sciences", "Weizmann", "Technion", "Tel Aviv University",
    "Seoul National", "University of Tokyo", "RIKEN", "Karolinska",
)
_ORG_RX = re.compile(
    r"(?<![A-Za-z])(" + "|".join(re.escape(k) for k in sorted(KNOWN_ORGS, key=len, reverse=True)) + r")(?![a-z])")
_BOILER = re.compile(
    r"(?i)published in partnership|in partnership with|article in press|check for updates"
    r"|doi\.org|^npj\b|^nature\b|\bjournal\b|copyright|licen[sc]e")


def _header(text: str) -> str:
    lines = [ln for ln in (text or "")[:5000].split("\n") if not _BOILER.search(ln)]
    t = "\n".join(lines)
    m = re.search(r"\n\s*(A\s?BSTRACT|Abstract|ABSTRACT)\b", t)
    if m and m.start() > 40:
        return t[: min(m.start(), 2500)]
    return t[:1200]


def _lookup_work(doi: str, arxiv_id: str, title: str):
    client = getattr(state(), "openalex_client", None)
    if client is None:
        return None
    try:
        if doi:
            work = client.fetch_work_by_doi(doi)
            if work is not None:
                return work
        if arxiv_id:
            work = client.fetch_work_by_doi(f"10.48550/arXiv.{arxiv_id}")
            if work is not None:
                return work
        if title and len(title) >= 20:
            return client.fetch_work_by_title(title)
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment
        LOGGER.info("card facts: OpenAlex lookup failed: %s", exc)
    return None


def author_facts(text: str, *, doi: str = "", arxiv_id: str = "", title: str = "") -> dict[str, Any]:
    header = _header(text or "")
    out: dict[str, Any] = {
        "affiliations": sorted({m.group(1) for m in _ORG_RX.finditer(header)}),
        "max_h_index": None, "h_source": None,
        "last_author": None,  # {"name", "h_index"|None}: the last author's OWN h, never the max
    }
    work = _lookup_work(doi, arxiv_id, title)  # fetch_work_by_doi normalizes internally
    if work is not None and work.max_author_h_index is not None:
        out["max_h_index"] = int(work.max_author_h_index)
        out["h_source"] = "openalex"
    if work is not None and work.last_author:
        out["last_author"] = dict(work.last_author)
    return out


__all__ = ["author_facts", "KNOWN_ORGS"]
