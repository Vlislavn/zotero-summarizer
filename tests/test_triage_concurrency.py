"""Concurrent gate-survivor scoring and source prompt routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import zotero_summarizer.services._common as common
from zotero_summarizer.models import SummarizeResponse
from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.triage.feeds import _triage as feeds
from zotero_summarizer.services.triage.prompts import DEFAULT_PRACTITIONER_TRIAGE_PROMPT


def _item(key: str) -> dict:
    return {"item_key": key, "item_id": int(key[1:]), "feed_library_id": 1,
            "title": f"Title {key}", "abstract": "abs"}


def _cand(tags=None):
    return SimpleNamespace(summary=SimpleNamespace(tags=tags or []))


def _run(items, outcomes_by_key, *, concurrency=4, provider=None):
    def fake_triage_one(item, *, log_prefix, triage_llm):
        return outcomes_by_key[item["item_key"]]

    with patch.object(feeds, "_triage_one", side_effect=fake_triage_one), \
         patch.object(common, "settings",
                      return_value=SimpleNamespace(triage_job_concurrency=concurrency)):
        return feeds._score_survivors(items, tick_id="t", triage_llm=None, provider=provider)


def _observed_workers(items, *, provider, concurrency):
    seen: dict = {}
    real_pool = feeds.ThreadPoolExecutor

    def spy(max_workers):
        seen["workers"] = max_workers
        return real_pool(max_workers=max_workers)

    outcomes = {it["item_key"]: (_cand(), None, False) for it in items}

    def fake_triage_one(item, *, log_prefix, triage_llm):
        return outcomes[item["item_key"]]

    with patch.object(feeds, "_triage_one", side_effect=fake_triage_one), \
         patch.object(common, "settings",
                      return_value=SimpleNamespace(triage_job_concurrency=concurrency)), \
         patch.object(feeds, "ThreadPoolExecutor", side_effect=spy):
        feeds._score_survivors(items, tick_id="t", triage_llm=None, provider=provider)
    return seen["workers"]


def test_partitions_triaged_fastreject_errors_and_fatal():
    items = [_item("K1"), _item("K2"), _item("K3"), _item("K4")]
    outcomes = {
        "K1": (_cand(tags=[]), None, False),
        "K2": (_cand(tags=["prefilter_low_corpus_affinity"]), None, False),
        "K3": (None, "boom", False),
        "K4": (None, "401 unauthorized", True),
    }
    triaged, fast_rejected, errors, fatal_seen = _run(items, outcomes)

    assert [it["item_key"] for it, _ in triaged] == ["K1"]
    assert [it["item_key"] for it, _ in fast_rejected] == ["K2"]
    assert sorted(it["item_key"] for it, _ in errors) == ["K3", "K4"]
    assert fatal_seen is True


def test_no_fatal_when_all_succeed():
    items = [_item("K1"), _item("K2")]
    outcomes = {"K1": (_cand(), None, False), "K2": (_cand(), None, False)}
    triaged, fast_rejected, errors, fatal_seen = _run(items, outcomes)
    assert len(triaged) == 2
    assert fast_rejected == [] and errors == []
    assert fatal_seen is False


def test_order_preserved_under_concurrency():
    items = [_item(f"K{i}") for i in range(1, 11)]
    outcomes = {it["item_key"]: (_cand(), None, False) for it in items}
    triaged, _, _, _ = _run(items, outcomes, concurrency=4)
    assert [it["item_key"] for it, _ in triaged] == [it["item_key"] for it in items]


def test_empty_input_is_noop():
    triaged, fast_rejected, errors, fatal_seen = _run([], {})
    assert triaged == [] and fast_rejected == [] and errors == []
    assert fatal_seen is False


def test_local_provider_forces_serial():
    items = [_item(f"K{i}") for i in range(1, 11)]
    local = ProviderConfig(name="mlx", base_url="http://127.0.0.1:8080/v1", api_key_env="K")
    assert _observed_workers(items, provider=local, concurrency=4) == 1


def test_remote_provider_uses_configured_cap():
    items = [_item(f"K{i}") for i in range(1, 11)]
    remote = ProviderConfig(name="remote", base_url="https://remote.example/v1", api_key_env="K")
    assert _observed_workers(items, provider=remote, concurrency=4) == 4


def test_hackernoon_uses_practitioner_prompt_only():
    seen = []

    def run(_req, **kwargs):
        seen.append(kwargs["triage_prompt_override"])
        return SummarizeResponse()

    with patch.object(feeds, "run_abstract_pipeline", side_effect=run), \
         patch.object(feeds, "_apply_prestige"), \
         patch.object(feeds.surprise_service, "compute_surprise_score", return_value=0.0):
        feeds._triage_one({**_item("K1"), "url": "https://www.hackernoon.com/story"}, log_prefix="x")
        feeds._triage_one({**_item("K2"), "url": "https://arxiv.org/abs/1"}, log_prefix="x")

    assert seen == [DEFAULT_PRACTITIONER_TRIAGE_PROMPT, None]
