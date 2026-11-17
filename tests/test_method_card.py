"""Method card: every shown quote is in the paper (С2), a build never touches the review (С5),
one job per paper with a 300 s retry window."""
import json
from types import SimpleNamespace

import pytest

from zotero_summarizer.models.providers import DefaultModelConfig, LLMRoutingConfig, ProviderConfig
from zotero_summarizer.services.library import _paper_read_pdf, _review_cache
from zotero_summarizer.services.library.method_card import _job, _prompt, _store, _verify
from zotero_summarizer.services.library._grounding import quote_is_grounded

CORE = "Glyph turns frozen foundation model embeddings into sparse interpretable clinical concepts using sparse autoencoders."
CMP = "Adding ten Glyph concepts to literature markers raised held-out AUC from 0.749 to 0.774 over literature markers."
NEW = "We argue that interpretability can extend beyond a mechanistic understanding of neural networks."
STEP = "Per-modality sparse autoencoders are trained on frozen embeddings to produce sparse features."
BODY = f"Introduction\n{NEW} {CORE}\n\nResults\n{CMP}\n\nMethods\n{STEP}\n"
SECTIONS = [
    {"id": "s1", "title": "Introduction", "page": 1, "text": f"{NEW} {CORE}"},
    {"id": "s2", "title": "Results", "page": 5, "text": CMP},
    {"id": "s3", "title": "Methods", "page": 3, "text": STEP},
    {"id": "s4", "title": "References", "page": 9, "text": "Smith 2020. Invented sentence that only the references contain here."},
]


def _raw(**over):
    data = {
        "core_idea": {"text": "Glyph turns frozen foundation model embeddings into sparse interpretable clinical concepts.",
                      "quote": CORE},
        "compared": [{"what": "literature markers", "result": "AUC 0.749 to 0.774", "quote": CMP},
                     {"what": "Phecodes", "result": "AUC 0.9 to 0.95", "quote": CMP},  # numbers not in the quote
                     {"what": "invented baseline", "result": "0.749", "quote": CMP}],  # name not in the quote
        "novelty_claim": {"quote": NEW},
        "reuse": [{"input": "frozen embeddings", "operation": "per-modality sparse autoencoders are trained",
                   "output": "sparse features", "quote": STEP},
                  {"input": "x", "operation": "a fabricated operation never described", "output": "y",
                   "quote": "This sentence does not occur anywhere in the paper text at all."}],
        "use_when": "you already have frozen embeddings and want named concepts",
        "caveat": {"text": "AUC rises only modestly", "quote": "This invented caveat sentence is not in the paper body."},
    }
    data.update(over)
    return _prompt.CardResponse.model_validate(data)


def test_every_shown_quote_is_found_in_the_paper_and_unsupported_rows_are_dropped():
    card = _verify.verify_card(_raw(), SECTIONS, BODY)
    quotes = [card["core_idea"]["quote"], card["novelty"]["claim_quote"],
              *(r["quote"] for r in card["compared"]), *(r["quote"] for r in card["reuse"])]
    assert quotes and all(quote_is_grounded(q, BODY, fuzzy=False) for q in quotes)
    assert [r["what"] for r in card["compared"]] == ["literature markers"]
    assert len(card["reuse"]) == 1 and card["caveat"] is None
    assert card["compared"][0]["section"] == "§ Results" and card["core_idea"]["section"] == "§ Introduction"
    assert card["novelty"]["check_state"] == "not_run" and card["novelty"]["checks"] == []
    assert card["short_text"].startswith("Glyph turns") and "Use it when you already have" in card["short_text"]


def test_quote_only_in_the_reference_list_gets_no_section():
    refs = SECTIONS[3]["text"]
    raw = _raw(novelty_claim={"quote": refs})
    assert _verify.verify_card(raw, SECTIONS, BODY + refs)["novelty"]["section"] == ""


def test_ungrounded_core_idea_fails_the_whole_card():
    raw = _raw(core_idea={"text": "Glyph cures every disease.", "quote": "Glyph cures every disease in the world today."})
    with pytest.raises(_verify.CardNotGrounded):
        _verify.verify_card(raw, SECTIONS, BODY)


class _Llm:
    def __init__(self, response):
        self.response, self.calls = response, 0

    def pydantic_prompt(self, *, prompt, pydantic_model):
        self.calls += 1
        assert CMP in prompt and STEP in prompt and '{{' not in prompt  # the whole body is the input
        return self.response


@pytest.fixture
def env(monkeypatch, tmp_path):
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF")
    routing = LLMRoutingConfig(providers=[ProviderConfig(name="p", base_url="https://example.test/v1", api_key_env="K")],
                               default=DefaultModelConfig(provider="p", model="m"))
    app = SimpleNamespace(
        pdf_extractor=SimpleNamespace(extract_text=lambda _p: BODY),
        resolve_stage_provider=lambda _s: SimpleNamespace(is_local=False),
        app_state=SimpleNamespace(config=SimpleNamespace(llm_routing=routing, quality_review=SimpleNamespace(
            use_docling=False, max_text_chars=60_000, context_window_tokens=256_000, map_chunk_chars=20_000))))
    llm = _Llm(_raw())
    monkeypatch.setattr(_job, "state", lambda: app)
    monkeypatch.setattr(_job, "_source_sha", lambda _p: "abcdef0123456789")
    monkeypatch.setattr(_job.factory, "resolve_stage_client", lambda *_a, **_k: llm)
    monkeypatch.setattr(_store, "settings", lambda: SimpleNamespace(paper_render_dir=tmp_path / "render"))
    monkeypatch.setattr(_paper_read_pdf, "extract_pdf_content", lambda *_a, **_k: {"sections": SECTIONS})
    monkeypatch.setattr(_job, "same_pdf_entry", lambda _e: True)
    entry = {"review_identity": {"source_path": str(pdf), "source_sha256": "abcdef0123456789"}}
    monkeypatch.setattr(_job.deep_review, "get_usable_review", lambda _k: entry)
    _job._RUNNING.clear()
    _job._FAILED.clear()
    return SimpleNamespace(entry=entry, llm=llm, tmp=tmp_path, app=app)


def test_build_writes_a_sidecar_and_leaves_the_review_cache_untouched(env, monkeypatch):
    cache = env.tmp / "deep_reviews.json"
    cache.write_text(json.dumps({"updated_at": "t", "reviews": {"K1": {"digest": {"tldr": "x"}}},
                                 "review_revisions": {"K1": "rev-1"}}), encoding="utf-8")
    monkeypatch.setattr(_review_cache, "_cache_path", lambda: cache)
    before = (cache.read_bytes(), _review_cache.publication_snapshot("K1"))
    stamp = _store.make_stamp("abcdef0123456789", _prompt.prompt_sha(), "m")
    _job._build("K1", env.entry, "abcdef0123456789", stamp, "m")
    assert (cache.read_bytes(), _review_cache.publication_snapshot("K1")) == before
    assert before[1][1] == "rev-1"
    stored = _store.read("K1", "abcdef0123456789", stamp)
    assert stored["state"] == "ready" and stored["model"] == "m" and stored["timing"]["chars"] > 400
    assert [f.name for f in (env.tmp / "render" / "_method_card").iterdir()] == ["K1-abcdef0123456789.json"]
    assert _store.read("K1", "abcdef0123456789", stamp + "x") is None  # another prompt/model = a miss


def test_one_job_per_key_and_failures_wait_300_seconds(env, monkeypatch):
    submitted = []
    monkeypatch.setattr(_job._POOL, "submit", lambda fn, *args: submitted.append(args))
    assert _job.request("K1", start=False)["reason"] == "not_built"
    assert _job.request("K1", start=True)["state"] == "building"
    assert _job.request("K1", start=True)["state"] == "building" and len(submitted) == 1
    _job._RUNNING.clear()
    _job._FAILED["K1"] = {"stamp": submitted[0][3], "at": _job.time.monotonic(), "reason": "model_error"}
    answer = _job.request("K1", start=True)
    assert answer["state"] == "failed" and not answer["can_build"] and answer["retry_in_seconds"] > 280
    assert len(submitted) == 1
    _job._FAILED["K1"]["at"] -= 301
    assert _job.request("K1", start=False)["can_build"] is True
    assert _job.request("K1", start=True)["state"] == "building" and len(submitted) == 2


def test_too_long_paper_is_unavailable_not_truncated(env):
    env.app.app_state.config.quality_review.context_window_tokens = 1
    env.app.app_state.config.quality_review.max_text_chars = 10
    stamp = _store.make_stamp("abcdef0123456789", _prompt.prompt_sha(), "m")
    _job._RUNNING.add("K1")
    _job._run("K1", env.entry, "abcdef0123456789", stamp, "m")
    assert env.llm.calls == 0
    assert _job._FAILED["K1"]["reason"] == "too_long" and "K1" not in _job._RUNNING
    assert _job.request("K1", start=True) == {"state": "unavailable", "reason": "too_long", "can_build": False,
                                              "retry_in_seconds": 0, "card": None}
