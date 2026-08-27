"""Shared fakes for the reading-queue test modules."""
from types import SimpleNamespace

from zotero_summarizer.services.library import reading_queue


class FakeReader:
    def __init__(self, items):
        self._items = items

    def get_items(self, **kwargs):
        raise AssertionError("reading_queue must use get_all_items, not get_items")

    def get_all_items(
        self, *, collection_key=None, search=None, tag=None, page_size=500,
        include_abstract=True,
    ):
        return {"items": self._items, "total": len(self._items)}


class Pred:
    def __init__(self, item_key, raw_score, shap=None, aux=None):
        self.item_key = item_key
        self.raw_score = raw_score
        self.shap_contribs = shap or []
        self.aux_context = aux or {}


class FakeGate:
    def __init__(self, sha, scores=None):
        self.golden_csv_sha256 = sha
        self._scores = scores or {}

    def predict(self, items, *, corpus_db_path, goals_config, return_shap=False, prestige_network=True):
        return [
            Pred(
                it["item_key"], self._scores.get(it["item_key"], 3.0),
                shap=[
                    {"feature": "semantic_match_specter2", "contribution": 0.5},
                    {"feature": "bias", "contribution": 2.0},
                ],
                aux={"max_author_h_index": 20},
            )
            for it in items
        ]


def item(key, pri="", date="2026-05-01", tags=()):
    return {
        "item_key": key, "title": f"T{key}", "abstract": "abs", "authors": "A",
        "reading_priority": pri, "has_pdf": True, "date_added": date, "tags": list(tags),
    }


def isolate(monkeypatch, tmp_path):
    from zotero_summarizer.services.library.review_fleet import verdict_store
    from zotero_summarizer.storage import repositories

    reading_queue.finish(error=None)
    monkeypatch.setattr(reading_queue, "_cache_path", lambda: tmp_path / "rq.json")
    monkeypatch.setattr(reading_queue, "run_in_background", lambda target: None)
    monkeypatch.setattr(
        reading_queue, "get_settings",
        lambda: SimpleNamespace(corpus_db_path=tmp_path / "c.db", triage_db_path=tmp_path / "t.db"),
    )
    monkeypatch.setattr(repositories, "list_label_verdict_priorities", lambda db_path: {})
    monkeypatch.setattr(verdict_store, "read_all", lambda: {})


def patch_state(monkeypatch, reader, gate):
    monkeypatch.setattr(
        reading_queue, "get_state",
        lambda: SimpleNamespace(
            zotero_reader=reader,
            classifier_gate=gate,
            app_state=SimpleNamespace(config=object()),
        ),
    )


def seed(sha, **scores):
    reading_queue._write_cache(sha, {
        key: {
            "relevance_score": value,
            "why_reason": "Topic match",
            "scoring": {"composite_score": value, "shap_top": []},
        }
        for key, value in scores.items()
    })
