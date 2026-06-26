"""Concurrent gate-survivor scoring (`feeds._score_survivors`).

The backlog drain's bottleneck is the per-item LLM call, so survivors are now
scored on a thread pool. These tests pin the contract that matters: the
partition into triaged / fast-rejected / errors is identical to the old
sequential path, order is preserved, and a fatal LLM error is surfaced so the
drain can stop instead of spinning.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import zotero_summarizer.services._common as common
from zotero_summarizer.models.providers import ProviderConfig
from zotero_summarizer.services.triage.feeds import _triage as feeds


def _item(key: str) -> dict:
    return {"item_key": key, "item_id": int(key[1:]), "feed_library_id": 1,
            "title": f"Title {key}", "abstract": "abs"}


def _cand(tags=None):
    """Minimal stand-in: _score_survivors only reads cand.summary.tags."""
    return SimpleNamespace(summary=SimpleNamespace(tags=tags or []))


def _run(items, outcomes_by_key, *, concurrency=4, provider=None):
    """Call _score_survivors with _triage_one mocked to a per-key outcome.

    Concurrency now flows through ``effective_llm_concurrency`` (in
    ``services._common``), so we patch that module's ``settings``. ``provider``
    defaults to None → remote/configured branch (matches the legacy path)."""
    def fake_triage_one(item, *, log_prefix, triage_llm):
        return outcomes_by_key[item["item_key"]]

    with patch.object(feeds, "_triage_one", side_effect=fake_triage_one), \
         patch.object(common, "settings",
                      return_value=SimpleNamespace(triage_job_concurrency=concurrency)):
        return feeds._score_survivors(items, tick_id="t", triage_llm=None, provider=provider)


def _observed_workers(items, *, provider, concurrency):
    """Run _score_survivors and capture the ThreadPoolExecutor max_workers."""
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
        "K1": (_cand(tags=[]), None, False),                              # triaged
        "K2": (_cand(tags=["prefilter_low_corpus_affinity"]), None, False),  # fast-reject
        "K3": (None, "boom", False),                                     # error, non-fatal
        "K4": (None, "401 unauthorized", True),                          # error, FATAL
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
    # Even with a thread pool, triaged results must follow input order.
    items = [_item(f"K{i}") for i in range(1, 11)]
    outcomes = {it["item_key"]: (_cand(), None, False) for it in items}
    triaged, _, _, _ = _run(items, outcomes, concurrency=4)
    assert [it["item_key"] for it, _ in triaged] == [it["item_key"] for it in items]


def test_empty_input_is_noop():
    triaged, fast_rejected, errors, fatal_seen = _run([], {})
    assert triaged == [] and fast_rejected == [] and errors == []
    assert fatal_seen is False


def test_local_provider_forces_serial():
    # A loopback provider must run the pool serially (1) regardless of the
    # configured cap — one on-device model can't absorb concurrent inference.
    items = [_item(f"K{i}") for i in range(1, 11)]
    local = ProviderConfig(name="mlx", base_url="http://127.0.0.1:8080/v1", api_key_env="K")
    assert _observed_workers(items, provider=local, concurrency=4) == 1


def test_remote_provider_uses_configured_cap():
    items = [_item(f"K{i}") for i in range(1, 11)]
    remote = ProviderConfig(name="kather", base_url="https://api.kather.ai/v1", api_key_env="K")
    assert _observed_workers(items, provider=remote, concurrency=4) == 4
