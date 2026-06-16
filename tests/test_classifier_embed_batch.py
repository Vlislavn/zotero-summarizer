"""Batched + device-accelerated SPECTER2 embedding (classifier_embed).

The real encoder is heavy (downloads ~450MB), so these tests mock the forward
pass and exercise the parts that carry the throughput + correctness contract:
the cache-aware batch wrapper (order + encode-misses-once), the 1-item shim, and
the MPS→CPU device fallback.
"""
from __future__ import annotations

import threading
import time
from contextlib import nullcontext

import numpy as np

from zotero_summarizer.services.model import classifier_embed as embed


def test_get_or_compute_embeddings_batch_caches_and_preserves_order(tmp_path, monkeypatch):
    db = tmp_path / "corpus.db"
    calls = {"n": 0}

    def fake_batch(pairs, *, sub_batch=32):
        calls["n"] += 1
        # Deterministic: first dim = title length, so we can assert order.
        return np.array([[float(len(t))] + [0.0] * 767 for t, _a in pairs], dtype=np.float32)

    monkeypatch.setattr(embed, "compute_embeddings_batch", fake_batch)
    items = [{"item_key": f"k{i}", "title": "x" * i, "abstract": "a"} for i in (1, 2, 3)]

    out1 = embed.get_or_compute_embeddings_batch(db, items)
    assert out1.shape == (3, 768)
    assert [out1[i, 0] for i in range(3)] == [1.0, 2.0, 3.0]  # order preserved
    assert calls["n"] == 1  # all misses → exactly one batched encode

    out2 = embed.get_or_compute_embeddings_batch(db, items)
    assert calls["n"] == 1  # all cache hits → no re-encode
    assert np.allclose(out1, out2)


def test_batch_encodes_only_the_misses(tmp_path, monkeypatch):
    db = tmp_path / "corpus.db"
    seen_pairs: list[list] = []

    def fake_batch(pairs, *, sub_batch=32):
        seen_pairs.append([t for t, _a in pairs])
        return np.ones((len(pairs), 768), dtype=np.float32)

    monkeypatch.setattr(embed, "compute_embeddings_batch", fake_batch)
    embed.get_or_compute_embeddings_batch(db, [{"item_key": "k1", "title": "A", "abstract": "x"}])
    # Second call: k1 is cached, k2 is new → only k2 should be encoded.
    embed.get_or_compute_embeddings_batch(
        db,
        [{"item_key": "k1", "title": "A", "abstract": "x"},
         {"item_key": "k2", "title": "B", "abstract": "y"}],
    )
    assert seen_pairs == [["A"], ["B"]]


def test_compute_embedding_is_batch_shim(monkeypatch):
    captured = {}

    def fake_batch(pairs, *, sub_batch=32):
        captured["pairs"] = pairs
        return np.arange(768 * len(pairs), dtype=np.float32).reshape(len(pairs), 768)

    monkeypatch.setattr(embed, "compute_embeddings_batch", fake_batch)
    v = embed.compute_embedding("title", "abstract")
    assert v.shape == (768,)
    assert v[0] == 0.0
    assert captured["pairs"] == [("title", "abstract")]


# --- device fallback -------------------------------------------------------

class _FakeTensor:
    def __init__(self, n, device="cpu"):
        self.n = n
        self.device = device

    def to(self, device):
        return _FakeTensor(self.n, device)


class _FakeCLS:
    def __init__(self, n):
        self.n = n

    def cpu(self):
        return self

    def numpy(self):
        return np.ones((self.n, 768), dtype=np.float32)


class _FakeHidden:
    def __init__(self, n):
        self.n = n

    def __getitem__(self, idx):  # last_hidden_state[:, 0, :]
        return _FakeCLS(self.n)


class _FakeOutputs:
    def __init__(self, n):
        self.last_hidden_state = _FakeHidden(n)


class _FakeModel:
    def __init__(self):
        self.moved_to: list[str] = []

    def to(self, device):
        self.moved_to.append(device)

    def __call__(self, **inputs):
        dev = next(iter(inputs.values())).device
        if dev == "mps":
            raise RuntimeError("mps: op not implemented")
        return _FakeOutputs(next(iter(inputs.values())).n)


class _FakeTorch:
    @staticmethod
    def no_grad():
        return nullcontext()


def _fake_tok(texts, **kwargs):
    return {"input_ids": _FakeTensor(len(texts))}


def test_encode_chunk_falls_back_from_mps_to_cpu(monkeypatch):
    monkeypatch.setitem(embed._MODEL_CACHE, "device", "mps")
    mdl = _FakeModel()
    out = embed._encode_chunk(_fake_tok, mdl, _FakeTorch, ["t1", "t2"], "mps")
    assert out.shape == (2, 768)
    assert "cpu" in mdl.moved_to                       # model moved to cpu after the mps failure
    assert embed._MODEL_CACHE["device"] == "cpu"       # sticks to cpu for the rest of the run


def test_encode_chunk_reraises_on_cpu_failure():
    class _AlwaysRaises(_FakeModel):
        def __call__(self, **inputs):
            raise RuntimeError("genuine failure")

    try:
        embed._encode_chunk(_fake_tok, _AlwaysRaises(), _FakeTorch, ["t"], "cpu")
    except RuntimeError as exc:
        assert "genuine failure" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected the CPU-path error to propagate (fail-fast)")


# --- load thread-safety (regression: meta-tensor race) ---------------------

def test_load_specter2_loads_once_under_concurrency(monkeypatch):
    """Concurrent first-loads must materialize SPECTER2 exactly ONCE.

    Two threads racing into `_load_specter2` used to both call
    `from_pretrained`; transformers' meta-device init patches global torch state
    and isn't thread-safe, so the second load left params on the `meta` device →
    `mdl.to(device)` raised "Cannot copy out of meta tensor" in the gate-retrain
    worker. The `_LOAD_LOCK` + double-checked cache must serialize them.
    """
    embed._MODEL_CACHE.clear()
    n_constructs = {"n": 0}

    class _FakeMdl:
        def eval(self):
            ...

        def to(self, device):
            ...

        def load_adapter(self, *a, **k):
            ...

    def _slow_from_pretrained(*a, **k):
        n_constructs["n"] += 1
        time.sleep(0.05)  # widen the window so the threads overlap inside the load
        return _FakeMdl()

    monkeypatch.setattr("adapters.AutoAdapterModel.from_pretrained",
                        staticmethod(_slow_from_pretrained))
    monkeypatch.setattr("transformers.AutoTokenizer.from_pretrained",
                        staticmethod(lambda *a, **k: object()))
    monkeypatch.setattr(embed, "_select_device", lambda torch: "cpu")

    try:
        threads = [threading.Thread(target=embed._load_specter2) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Before the lock this was ~8 (and on the real model, the meta corruption).
        assert n_constructs["n"] == 1
    finally:
        embed._MODEL_CACHE.clear()
