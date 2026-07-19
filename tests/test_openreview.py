"""OpenReview leaf: auth gating, accept/venue/year filter, tier + rating parse,
re-login retry, and the errors→[] boundary contract."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from zotero_summarizer.integrations.openreview import (
    OpenReviewClient,
    _classify_tier,
    _is_accepted,
    _parse_rating,
    search_openreview,
)

_HOSTS = ("ICLR.cc", "NeurIPS.cc", "MIDL.io")


def _resp(status: int, payload: dict | None = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.json.return_value = payload or {}
    return r


def _client(http: MagicMock, **kw) -> OpenReviewClient:
    kw.setdefault("username", "u@x.z")
    kw.setdefault("password", "pw")
    return OpenReviewClient(venue_hosts=_HOSTS, year_min=2024, http_client=http, **kw)


def _paper_note(fid, title, venue, venueid, abstract="an abstract here", authors=("Alice B",)):
    return {"id": fid, "content": {
        "title": {"value": title}, "venue": {"value": venue},
        "venueid": {"value": venueid}, "abstract": {"value": abstract},
        "authors": {"value": list(authors)},
    }}


def _review(rating):
    return {"invitations": ["ICLR.cc/2025/Conference/-/Official_Review"],
            "content": {"rating": {"value": rating}}}


# ---------------------------------------------------------------- pure helpers

def test_classify_tier_priority():
    conf = "ICLR.cc/2025/Conference"
    assert _classify_tier("ICLR 2025 Oral", conf) == "oral"
    assert _classify_tier("NeurIPS 2024 Spotlight", "NeurIPS.cc/2024/Conference") == "spotlight"
    assert _classify_tier("ICLR 2025 Poster", conf) == "poster"
    assert _classify_tier("MIDL 2025 Accept", "MIDL.io/2025/Conference") == "poster"


def test_workshop_detected_from_venueid_not_its_poster_label():
    # A workshop's own "Poster"/"Spotlight" label must NOT read as a main-conf tier.
    assert _classify_tier("SCOPE - ICLR 2025 Poster", "ICLR.cc/2025/Workshop/SCOPE") == "workshop"
    assert _classify_tier("MTI-LLM @ NeurIPS 2025 Spotlight",
                          "NeurIPS.cc/2025/Workshop/MTI-LLM") == "workshop"
    # Also caught when only the venue string says "Workshop".
    assert _classify_tier("ICML 2024 Workshop XYZ", "ICML.cc/2024/Workshop/XYZ") == "workshop"


def test_is_accepted():
    assert _is_accepted("ICLR 2025 Oral")
    assert _is_accepted("NeurIPS 2024 Poster")
    assert not _is_accepted("Submitted to ICLR 2025")
    assert not _is_accepted("ICLR 2025 Reject")
    assert not _is_accepted("Withdrawn")


def test_parse_rating():
    assert _parse_rating(8) == 8.0
    assert _parse_rating(7.5) == 7.5
    assert _parse_rating("8: accept, good paper") == 8.0
    assert _parse_rating("marginal") is None
    assert _parse_rating(None) is None
    assert _parse_rating(True) is None  # bool is not a rating


# --------------------------------------------------------------- auth gating

def test_disabled_without_creds_returns_empty_no_network():
    http = MagicMock()
    client = OpenReviewClient(venue_hosts=_HOSTS, year_min=2024, http_client=http,
                              username="", password="")
    assert client.enabled is False
    assert search_openreview(client, "rag") == []
    http.post.assert_not_called()
    http.get.assert_not_called()


def test_disabled_when_offline():
    http = MagicMock()
    client = _client(http, allow_network=False)
    assert client.enabled is False
    assert search_openreview(client, "rag") == []
    http.get.assert_not_called()


# --------------------------------------------------------------- happy path

def test_accepted_oral_hit_with_rating():
    http = MagicMock()
    http.post.return_value = _resp(200, {"token": "T"})
    http.get.side_effect = [
        _resp(200, {"notes": [_paper_note(
            "F1", "Deep RAG", "ICLR 2025 Oral", "ICLR.cc/2025/Conference")]}),
        _resp(200, {"notes": [_review(8), _review(6), _review(8)]}),
    ]
    hits = search_openreview(_client(http), "retrieval augmented generation", with_ratings=True)
    assert len(hits) == 1
    h = hits[0]
    assert h.title == "Deep RAG" and h.tier == "oral" and h.decision == "accepted"
    assert h.n_reviews == 3 and abs(h.mean_rating - 7.3333) < 1e-3
    assert h.year == 2025 and h.forum_id == "F1"
    assert h.url == "https://openreview.net/forum?id=F1"


def test_accepted_paper_no_public_reviews():
    http = MagicMock()
    http.post.return_value = _resp(200, {"token": "T"})
    http.get.side_effect = [
        _resp(200, {"notes": [_paper_note(
            "F2", "No Reviews", "MIDL 2025 Poster", "MIDL.io/2025/Conference")]}),
        _resp(200, {"notes": []}),  # reviews hidden
    ]
    hits = search_openreview(_client(http), "segmentation", with_ratings=True)
    assert len(hits) == 1
    assert hits[0].mean_rating is None and hits[0].n_reviews == 0


def test_ratings_gated_off_by_default_no_forum_fanout():
    # Production (with_ratings omitted) reads only the venue+tier chip — it must NOT
    # issue the per-paper forum_reviews GET. The single GET here is /notes/search.
    http = MagicMock()
    http.post.return_value = _resp(200, {"token": "T"})
    http.get.side_effect = [
        _resp(200, {"notes": [_paper_note(
            "F3", "Gated Ratings", "ICLR 2025 Oral", "ICLR.cc/2025/Conference")]}),
    ]
    hits = search_openreview(_client(http), "retrieval")
    assert len(hits) == 1
    h = hits[0]
    assert h.tier == "oral" and h.mean_rating is None and h.n_reviews == 0
    assert http.get.call_count == 1  # search only, no per-paper forum fan-out


# ------------------------------------------------------------------- filters

def test_rejected_and_offvenue_and_old_dropped():
    http = MagicMock()
    http.post.return_value = _resp(200, {"token": "T"})
    http.get.side_effect = [
        _resp(200, {"notes": [
            _paper_note("R", "Rej", "Submitted to ICLR 2025", "ICLR.cc/2025/Conference"),
            _paper_note("V", "OffVenue", "SomeWorkshop 2025 Poster", "randomsite.org/2025"),
            _paper_note("Y", "TooOld", "ICLR 2020 Oral", "ICLR.cc/2020/Conference"),
        ]}),
    ]
    # None survive the filters, so no forum call is needed.
    assert search_openreview(_client(http), "q") == []


# --------------------------------------------------------------- error paths

def test_transport_error_returns_empty():
    http = MagicMock()
    http.post.return_value = _resp(200, {"token": "T"})
    http.get.side_effect = httpx.ConnectError("boom")
    assert search_openreview(_client(http), "q") == []


def test_relogin_on_401_then_retry():
    http = MagicMock()
    http.post.return_value = _resp(200, {"token": "T"})
    http.get.side_effect = [_resp(401), _resp(200, {"notes": []})]
    client = _client(http)
    assert client.search("q", limit=10) == []
    assert http.post.call_count == 2  # initial login + one re-login
    assert http.get.call_count == 2


def test_forum_reviews_cached(tmp_path):
    from zotero_summarizer.integrations.openalex_cache import OpenAlexCache
    cache = OpenAlexCache(tmp_path / "c.db", ttl_seconds=86400)
    http = MagicMock()
    http.post.return_value = _resp(200, {"token": "T"})
    http.get.side_effect = [_resp(200, {"notes": [_review(9), _review(7)]})]
    client = _client(http, cache=cache)
    assert client.forum_reviews("F9") == (8.0, 2)
    # Second call served from cache — no extra network.
    assert client.forum_reviews("F9") == (8.0, 2)
    assert http.get.call_count == 1
