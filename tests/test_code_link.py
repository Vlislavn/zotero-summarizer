"""Code-repository extraction + validation for the deep-review brief.

Network is always stubbed: ``find_code_link`` takes an injectable ``checker`` and
``github.check_repo`` is exercised against a monkeypatched ``httpx.get``.
"""
from __future__ import annotations

import fitz
import httpx

from zotero_summarizer.integrations import github
from zotero_summarizer.services.library import _code_link, _paper_read_pdf


def _live(desc="", title=""):
    return github.RepoCheck(exists=True, status=200, description=desc, title=title)


# ---------------------------------------------------------------------------
# extraction + ranking
# ---------------------------------------------------------------------------


def test_extract_excludes_reserved_owners_and_dedupes():
    text = (
        "Baseline https://github.com/facebookresearch/detectron2 and again "
        "github.com/facebookresearch/detectron2 . CI on github.com/features/actions."
    )
    keys = [f"{c['owner']}/{c['repo']}" for c in _code_link.extract_candidates(text)]
    assert keys == ["facebookresearch/detectron2"]  # reserved 'features' dropped, deduped


def test_availability_phrase_ranks_first():
    text = (
        "We compare to https://github.com/other/baseline among others. "
        "Our code is available at https://github.com/acme/ours."
    )
    cands = _code_link.extract_candidates(text)
    assert cands[0]["owner"] == "acme" and cands[0]["near_availability"] is True
    assert cands[1]["near_availability"] is False


def test_repo_segment_stops_at_path_and_strips_punctuation():
    text = "see https://github.com/acme/ours/blob/main/train.py, and https://github.com/x/y.git."
    by_owner = {c["owner"]: c["repo"] for c in _code_link.extract_candidates(text)}
    assert by_owner["acme"] == "ours"  # path stripped
    assert by_owner["x"] == "y"        # trailing ".git" + "." stripped


# ---------------------------------------------------------------------------
# find_code_link: relevance, liveness preference, contracts
# ---------------------------------------------------------------------------


def test_no_github_link_is_not_found():
    assert _code_link.find_code_link("a paper with no repositories", "Title")["found"] is False


def test_offline_marks_unchecked_and_skips_network():
    def boom(*a, **k):  # must never be called offline
        raise AssertionError("network hit while offline")

    res = _code_link.find_code_link(
        "code at https://github.com/acme/ours", "x", offline=True, checker=boom
    )
    assert res == {
        "found": True, "url": "https://github.com/acme/ours", "exists": None,
        "checked": False, "relevance": "unverified", "near_availability": True,
    }


def test_relevance_matched_via_title_overlap_without_availability_phrase():
    text = "Experiments run on https://github.com/acme/wonder-net heavily."  # no availability phrase
    res = _code_link.find_code_link(
        text, "WonderNet: a wonder network for triage", offline=False,
        checker=lambda o, r: _live(desc="WonderNet wonder network for paper triage"),
    )
    assert res["relevance"] == "matched" and res["title_overlap"] >= 2
    assert res["near_availability"] is False  # matched on description overlap alone


def test_unrelated_live_repo_is_unverified():
    text = "We use https://github.com/numpy/numpy for arrays."
    res = _code_link.find_code_link(
        text, "A study of tumor microenvironments", offline=False,
        checker=lambda o, r: _live(desc="The fundamental package for scientific computing"),
    )
    assert res["exists"] is True and res["relevance"] == "unverified"


def test_live_repo_beats_dead_one():
    text = (
        "Our code is available at https://github.com/acme/dead. "
        "Mirror at https://github.com/acme/live."
    )

    def checker(owner, repo):
        return github.RepoCheck(False, 404) if repo == "dead" else _live(desc="mirror")

    res = _code_link.find_code_link(text, "unrelated title", offline=False, checker=checker)
    assert res["exists"] is True and res["url"].endswith("acme/live")


# ---------------------------------------------------------------------------
# integrations.github.check_repo (httpx stubbed)
# ---------------------------------------------------------------------------


def test_check_repo_parses_og_tags_on_200(monkeypatch):
    body = (
        '<meta property="og:title" content="acme/ours: a tool">'
        '<meta property="og:description" content="Great &amp; useful repo">'
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, text=body))
    res = github.check_repo("acme", "ours")
    assert res.exists is True and res.description == "Great & useful repo"
    assert res.title == "acme/ours: a tool"


# ---------------------------------------------------------------------------
# hyperlink-annotation harvest: a github link that's ONLY a clickable PDF link
# annotation (not in the visible text) is still caught by the code-link layer.
# ---------------------------------------------------------------------------


def test_extract_link_uris_harvests_annotation(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "See our code for details.")  # visible text has NO url
    page.insert_link({
        "kind": fitz.LINK_URI,
        "from": fitz.Rect(72, 66, 200, 78),
        "uri": "https://github.com/acme/hidden-repo",
    })
    pdf_path = tmp_path / "paper.pdf"
    doc.save(str(pdf_path))
    doc.close()

    content = _paper_read_pdf.extract_pdf_content(pdf_path)
    assert content["link_uris"] == ["https://github.com/acme/hidden-repo"]
    assert "github.com" not in content["full_text"]  # url is NOT in the visible text

    # linkless body → not found; body + harvested uri → found.
    body = content["full_text"]
    assert _code_link.find_code_link(body, "x", offline=True)["found"] is False
    code_text = body + "\n" + "\n".join(content["link_uris"])
    res = _code_link.find_code_link(code_text, "x", offline=True)
    assert res["found"] is True and res["url"].endswith("acme/hidden-repo")


def test_extract_link_uris_dedupes_and_rejects_whitespace_smuggling(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    # A benign repeated link (dedup + order) and a CRAFTED annotation whose value
    # packs a fake availability phrase behind an embedded newline (injection). The
    # second must be dropped, else the attacker's link would win code-link ranking.
    for i, uri in enumerate([
        "https://github.com/acme/real",
        "https://github.com/acme/real",  # duplicate → collapsed
        "https://github.com/other/dep",
        "https://github.com/attacker/evil\nOur code is available at "
        "https://github.com/attacker/evil2",  # whitespace → rejected
    ]):
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 72 + i * 12, 200, 84 + i * 12), "uri": uri})
    pdf_path = tmp_path / "crafted.pdf"
    doc.save(str(pdf_path))
    doc.close()

    uris = _paper_read_pdf.extract_pdf_content(pdf_path)["link_uris"]
    assert uris == ["https://github.com/acme/real", "https://github.com/other/dep"]  # deduped, ordered, no smuggle
    # The crafted availability phrase never reaches the code-link text, so it can't
    # promote the attacker repo above a bare in-text link.
    assert not any("attacker" in u for u in uris)


def test_check_repo_404_is_dead_and_transport_error_is_unknown(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(404))
    assert github.check_repo("acme", "gone").exists is False

    def raise_err(*a, **k):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", raise_err)
    unknown = github.check_repo("acme", "ours")
    assert unknown.exists is None and unknown.status is None
