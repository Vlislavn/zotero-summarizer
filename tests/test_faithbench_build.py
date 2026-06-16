"""faithbench build: the deterministic span keep-gate, trap absence rule, and
benchmark-file persistence (immutability, versioning, review CSV)."""
from __future__ import annotations

import json

import pytest

from zotero_summarizer.services.faithbench._build_claims import decompose_digest
from zotero_summarizer.services.faithbench._build_qa import (
    build_traps,
    generate_candidates,
    verify_candidates,
)
from zotero_summarizer.services.faithbench._corpus import (
    PaperRecord,
    chunk_text,
    normalize_text,
    sentence_at,
    sha256_text,
)
from zotero_summarizer.services.faithbench._dataset import (
    BenchmarkMeta,
    PaperManifestEntry,
    QAItem,
    benchmark_path,
    export_review_csv,
    load_benchmark,
    next_benchmark_version,
    save_benchmark,
)

TEXT_A = (
    "We evaluated GlassNet on the ImageNet dataset.\n"
    "Training used 1,281,167 images over 90 epochs.\n"
    "The top-1 accuracy reached 85.3 percent."
)
TEXT_B = (
    "This clinical study enrolled 412 patients across 7 hospitals. "
    "Median survival was 14.2 months under pembrolizumab."
)


def _paper(key: str, text: str) -> PaperRecord:
    return PaperRecord(item_key=key, title=f"Paper {key}", text=text,
                       text_sha256=sha256_text(text))


def _cand(question: str, span: str, answer_type: str = "span") -> dict:
    return {"question": question, "answer_span": span, "answer_type": answer_type}


# ---------------------------------------------------------------------------
# Keep-gate
# ---------------------------------------------------------------------------


def test_verbatim_span_kept_with_correct_offsets_and_evidence():
    paper = _paper("A", TEXT_A)
    kept = verify_candidates(
        [_cand("Which dataset was used?", "ImageNet")], paper=paper, max_keep=5
    )
    assert len(kept) == 1
    qa = kept[0]
    assert paper.text[qa.span_start: qa.span_end] == "ImageNet"
    assert "ImageNet" in qa.evidence_sentence
    assert qa.occurrences_in_paper == 1


def test_hallucinated_span_dropped():
    kept = verify_candidates(
        [_cand("What was the dataset?", "CIFAR-100")], paper=_paper("A", TEXT_A), max_keep=5
    )
    assert kept == []


def test_case_insensitive_and_whitespace_tolerant_anchoring():
    paper = _paper("A", TEXT_A)
    kept = verify_candidates(
        [
            _cand("Q1: which dataset?", "imagenet dataset"),
            # builder collapsed the line break between "dataset.\nTraining"
            _cand("Q2: how many images were used over how many epochs?",
                  "1,281,167 images over 90 epochs", "number"),
        ],
        paper=paper, max_keep=5,
    )
    assert len(kept) == 2
    for qa in kept:
        assert paper.text[qa.span_start: qa.span_end]  # anchored to real offsets


def test_gate_drops_leaky_long_duplicate_and_nonnumeric():
    paper = _paper("A", TEXT_A)
    kept = verify_candidates(
        [
            _cand("Does ImageNet appear in ImageNet?", "ImageNet"),       # leaky
            _cand("What is the whole text?", "x" * 200),                  # too long
            _cand("Which dataset was used?", "ImageNet"),                 # ok
            _cand("Which dataset was  used?", "ImageNet"),                # dup question
            _cand("What metric?", "accuracy reached", "number"),          # number w/o digits
        ],
        paper=paper, max_keep=5,
    )
    assert [qa.question for qa in kept] == ["Which dataset was used?"]


def test_max_keep_caps_and_ids_are_sequential():
    paper = _paper("A", TEXT_A)
    kept = verify_candidates(
        [
            _cand("Q dataset?", "ImageNet"),
            _cand("Q epochs?", "90 epochs"),
            _cand("Q accuracy?", "85.3 percent"),
        ],
        paper=paper, max_keep=2,
    )
    assert len(kept) == 2
    assert [qa.item_id for qa in kept] == ["qa:A:0", "qa:A:1"]


def test_generate_candidates_skips_unparseable_windows():
    class FlakyBuilder:
        def __init__(self):
            self.calls = 0

        def prompt(self, prompt, **kwargs):
            self.calls += 1
            return json.dumps({"items": [
                {"question": "Which dataset?", "answer_span": "ImageNet", "answer_type": "entity"}
            ]}) if self.calls > 1 else "I think the answer is... (no JSON here)"

    candidates = generate_candidates(
        FlakyBuilder(), title="T", text="x" * 13_000, per_window=3
    )
    assert candidates  # later windows still contribute


# ---------------------------------------------------------------------------
# Traps
# ---------------------------------------------------------------------------


def test_traps_only_use_absent_answers():
    paper_a, paper_b = _paper("A", TEXT_A), _paper("B", TEXT_B)
    qa_by_paper = {
        "A": verify_candidates(
            [_cand("Which dataset was used?", "ImageNet"),
             _cand("What accuracy was reached?", "85.3 percent", "number")],
            paper=paper_a, max_keep=5,
        ),
        "B": verify_candidates(
            [_cand("How many patients enrolled?", "412 patients", "number"),
             _cand("Which drug was studied?", "pembrolizumab")],
            paper=paper_b, max_keep=5,
        ),
    }
    traps = build_traps([paper_a, paper_b], qa_by_paper, traps_per_paper=2)
    for trap in traps:
        target_text = TEXT_A if trap.paper_item_key == "A" else TEXT_B
        assert normalize_text(trap.source_gold_answer) not in normalize_text(target_text)
        assert trap.source_paper_item_key != trap.paper_item_key
        assert trap.gold_behavior == "abstain"
    assert any(t.paper_item_key == "A" for t in traps)
    assert any(t.paper_item_key == "B" for t in traps)


def test_trap_skips_answers_with_token_overlap():
    shared = TEXT_B + " The control arm also received pembrolizumab."
    paper_a, paper_b = _paper("A", shared), _paper("B", TEXT_B)
    qa_b = verify_candidates(
        [_cand("Which drug was studied?", "pembrolizumab")], paper=paper_b, max_keep=5
    )
    traps = build_traps([paper_a, paper_b], {"A": [], "B": qa_b}, traps_per_paper=1)
    assert all(t.paper_item_key != "A" for t in traps)  # answer token present in A


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_benchmark_roundtrip_versioning_and_immutability(tmp_path):
    paper = _paper("A", TEXT_A)
    items = verify_candidates(
        [_cand("Which dataset was used?", "ImageNet")], paper=paper, max_keep=5
    )
    meta = BenchmarkMeta(
        version=1, created_at="t", builder_model="B",
        papers=[PaperManifestEntry(item_key="A", title="Paper A",
                                   text_sha256=paper.text_sha256, n_chars=len(TEXT_A))],
        config={"qa_per_paper": 5},
    )
    assert next_benchmark_version(tmp_path) == 1
    path = benchmark_path(tmp_path, 1)
    assert save_benchmark(path, meta, items) == 1
    assert next_benchmark_version(tmp_path) == 2

    with pytest.raises(FileExistsError):
        save_benchmark(path, meta, items)  # immutable

    loaded_meta, loaded_items = load_benchmark(path)
    assert loaded_meta.papers[0].text_sha256 == paper.text_sha256
    assert isinstance(loaded_items[0], QAItem)
    assert loaded_items[0].gold_answer == "ImageNet"
    assert loaded_meta.paper_by_key("A").n_chars == len(TEXT_A)
    with pytest.raises(KeyError):
        loaded_meta.paper_by_key("ZZZ")


def test_review_csv_contains_span_context(tmp_path):
    paper = _paper("A", TEXT_A)
    items = verify_candidates(
        [_cand("Which dataset was used?", "ImageNet")], paper=paper, max_keep=5
    )
    csv_path = tmp_path / "review.csv"
    export_review_csv(csv_path, items, {"A": TEXT_A})
    content = csv_path.read_text()
    assert "ImageNet" in content and "Which dataset was used?" in content


# ---------------------------------------------------------------------------
# Claim decomposition (field attribution)
# ---------------------------------------------------------------------------


_DIGEST = {
    "tldr": "GlassNet reaches 85.3 percent top-1 accuracy on ImageNet.",
    "read_why": "Tests agent autonomy limits in vision pipelines.",
}


class _ScriptedDecomposer:
    def __init__(self, claims):
        self.claims = claims

    def prompt(self, prompt, **kwargs):
        return json.dumps({"claims": self.claims})


def _decompose(tmp_path, claims):
    return decompose_digest(
        digest_dump=_DIGEST, digest_sha="f" * 64, title="T",
        decompose_llm=_ScriptedDecomposer(claims), cache_dir=tmp_path,
    )


def test_decomposer_field_tag_is_authoritative_over_token_overlap(tmp_path):
    # Claim text overlaps tldr far more than read_why — the tag must win.
    rows = _decompose(tmp_path, [
        {"field": "read_why", "claim": "GlassNet reaches strong accuracy on ImageNet."},
    ])
    assert rows == [
        {"field": "read_why", "claim": "GlassNet reaches strong accuracy on ImageNet."}
    ]


def test_untagged_or_unknown_field_falls_back_to_token_overlap(tmp_path):
    rows = _decompose(tmp_path, [
        "GlassNet reaches 85.3 percent top-1 accuracy on ImageNet.",  # legacy string
        {"field": "banana", "claim": "Tests agent autonomy limits in vision pipelines."},
        {"field": "tldr", "claim": "   "},  # blank claims are dropped
    ])
    assert [r["field"] for r in rows] == ["tldr", "read_why"]


# ---------------------------------------------------------------------------
# Corpus helpers used by the gate
# ---------------------------------------------------------------------------


def test_chunking_covers_text_and_respects_overlap():
    text = " ".join(f"word{i}" for i in range(2000))
    chunks = chunk_text(text, chunk_chars=500, overlap=100)
    assert all(len(c) <= 500 for c in chunks)
    assert "word0" in chunks[0] and "word1999" in chunks[-1]
    with pytest.raises(ValueError):
        chunk_text(text, chunk_chars=100, overlap=100)


def test_sentence_at_returns_containing_sentence():
    offset = TEXT_A.find("85.3")
    assert "85.3 percent" in sentence_at(TEXT_A, offset)
