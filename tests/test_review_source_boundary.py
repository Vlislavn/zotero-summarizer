"""Issue #34: replay reported outputs without bypassing production verification."""
from __future__ import annotations

import re

import pytest

from test_digest_verification import PAPER, _Verifier, _digest
from zotero_summarizer.services.library._digest_verification import verify_digest
from zotero_summarizer.services.library._map_reduce import map_reduce_digest
from zotero_summarizer.services.library.quality_review import assess_digest
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


HTTP_ACCESS = "Full text is inaccessible (HTTP 403 from MDPI);"
NO_ACCESS = (
    "No content retrievable; the original adds nothing beyond the digest "
    "until access is restored."
)


class _Generator:
    def __init__(self, *digests):
        self.digests = digests
        self.prompts = []

    def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
        self.prompts.append(prompt)
        return self.digests[min(len(self.prompts), len(self.digests)) - 1]


class _AccessVerifier(_Verifier):
    """Script the provider's semantic verdict, not the verification implementation."""

    def _checks(self, prompt):
        checks = super()._checks(prompt)
        for index, claim in re.findall(r"(?m)^\[(\d+)\] (.+)$", prompt):
            if any(part in claim for part in (
                "No content retrievable", "until access is restored",
                "No paper content retrievable",
            )):
                checks[int(index)].update(supported=False, evidence=[])
        return checks


class _Mapper(_Verifier):
    def __init__(self, note):
        super().__init__()
        self.note = note
        self.map_calls = 0
        self.verification_prompts = []

    def prompt(self, prompt):
        self.map_calls += 1
        return self.note

    def pydantic_prompt(self, *, prompt, **kwargs):
        self.verification_prompts.append(prompt)
        return super().pydantic_prompt(prompt=prompt, **kwargs)


@pytest.mark.parametrize("read_why, weakness, error", [
    (HTTP_ACCESS, "", "source-absent literals"),
    (NO_ACCESS, "No paper content retrievable;", "unsupported fields"),
])
def test_reported_failures_are_reproduced_by_real_verifier(read_why, weakness, error):
    bad = _digest().model_copy(update={"read_why": read_why, "key_weakness": weakness})
    with pytest.raises(ValueError, match=error):
        verify_digest(bad, PAPER, _AccessVerifier())


@pytest.mark.parametrize("prefix", [False, True], ids=["rank", "prefix"])
@pytest.mark.parametrize("read_why, weakness", [
    (HTTP_ACCESS, ""),
    (NO_ACCESS, "No paper content retrievable;"),
])
@pytest.mark.parametrize("corrected", [False, True], ids=["unresolved", "corrected"])
def test_reported_outputs_get_one_bounded_correction(prefix, read_why, weakness, corrected):
    bad = _digest().model_copy(update={"read_why": read_why, "key_weakness": weakness})
    generator = _Generator(bad, _digest() if corrected else bad)
    kwargs = dict(title="Paper", full_text=PAPER, config=_default_goals_config(),
                  llm=generator, verifier_llm=_AccessVerifier(), prefix=prefix)
    if corrected:
        result = assess_digest(**kwargs)
        assert result.read_why == _digest().read_why
    else:
        with pytest.raises(ValueError, match="Digest contains"):
            assess_digest(**kwargs)
    assert len(generator.prompts) == 2
    assert "failed source verification" in generator.prompts[1]


@pytest.mark.parametrize("field, claim", [
    ("read_why", HTTP_ACCESS),
    ("read_why", "Inspect the reported accuracy of 99%."),
    ("key_findings", ["The model reaches 99% accuracy."]),
])
def test_generated_map_notes_cannot_authorize_source_absent_numbers(field, claim):
    bad = _digest().model_copy(update={field: claim})
    note = claim if isinstance(claim, str) else claim[0]
    mapper, generator = _Mapper(note), _Generator(bad)
    with pytest.raises(ValueError, match="source-absent literals"):
        map_reduce_digest("Paper", PAPER, _default_goals_config(),
                          map_llm=mapper, reduce_llm=generator)
    assert len(generator.prompts) == 2
    assert mapper.calls == 0  # deterministic guard rejects before semantic verification


def test_map_reduce_correction_is_grounded_in_original_not_generated_notes():
    source = PAPER + " The evaluation includes 42 held-out samples."
    bad = _digest().model_copy(update={"read_why": HTTP_ACCESS})
    good = _digest().model_copy(update={"read_why": "Inspect the evaluation of 42 held-out samples."})
    mapper, generator = _Mapper(HTTP_ACCESS), _Generator(bad, good)
    result = map_reduce_digest("Paper", source, _default_goals_config(),
                               map_llm=mapper, reduce_llm=generator)
    assert result.read_why == good.read_why and result.basis == "map_reduce"
    assert len(generator.prompts) == 2 and mapper.calls == 1
    assert source in mapper.verification_prompts[0]
    assert HTTP_ACCESS not in mapper.verification_prompts[0]


def test_source_supported_http_number_in_read_why_is_not_blanket_rejected():
    source = "The paper evaluates an HTTP client that handles HTTP 403 responses."
    digest = _digest("The paper evaluates an HTTP client.").model_copy(
        update={"read_why": "Inspect how the client handles HTTP 403 responses."},
    )
    mapper, generator = _Mapper("The paper evaluates an HTTP client."), _Generator(digest)
    result = map_reduce_digest("Paper", source, _default_goals_config(),
                               map_llm=mapper, reduce_llm=generator)
    assert result.read_why == digest.read_why
    assert len(generator.prompts) == 1 and mapper.calls == 1


@pytest.mark.parametrize("note", ["", " \n\t"])
def test_empty_map_output_stops_before_digest_generation(note):
    mapper = _Mapper(note)
    bad = _digest().model_copy(update={"read_why": NO_ACCESS})
    generator = _Generator(bad)
    with pytest.raises(ValueError, match="empty chunk summary"):
        map_reduce_digest("Paper", PAPER, _default_goals_config(),
                          map_llm=mapper, reduce_llm=generator)
    assert mapper.map_calls == 1
    assert generator.prompts == [] and mapper.calls == 0


def test_partially_empty_mapping_does_not_silently_drop_source_coverage():
    class PartialMapper(_Mapper):
        def prompt(self, prompt):
            self.map_calls += 1
            return PAPER if self.map_calls == 1 else ""

    mapper, generator = PartialMapper(PAPER), _Generator(_digest())
    with pytest.raises(ValueError, match="empty chunk summary"):
        map_reduce_digest("Paper", PAPER * 150, _default_goals_config(),
                          map_llm=mapper, reduce_llm=generator)
    assert mapper.map_calls == 2
    assert generator.prompts == []


@pytest.mark.parametrize("prefix", [False, True], ids=["rank", "prefix"])
@pytest.mark.parametrize("source", ["", " \n\t"])
def test_empty_direct_source_never_becomes_a_skip_review(prefix, source):
    generator = _Generator(_digest().model_copy(update={"read_why": NO_ACCESS}))
    with pytest.raises(ValueError, match="non-empty source text"):
        assess_digest(title="Paper", full_text=source, config=_default_goals_config(),
                      llm=generator, verifier_llm=_AccessVerifier(), prefix=prefix)
    assert generator.prompts == []


@pytest.mark.parametrize("corrected", [False, True], ids=["unresolved", "corrected"])
def test_number_free_access_story_in_map_notes_is_not_paper_evidence(corrected):
    class AccessMapper(_Mapper):
        def _checks(self, prompt):
            return _AccessVerifier()._checks(prompt)

    bad = _digest().model_copy(update={"read_why": NO_ACCESS, "key_weakness": "No paper content retrievable;"})
    mapper = AccessMapper(NO_ACCESS)
    generator = _Generator(bad, _digest() if corrected else bad)
    kwargs = dict(title="Paper", full_text=PAPER, config=_default_goals_config(),
                  map_llm=mapper, reduce_llm=generator)
    if corrected:
        assert map_reduce_digest(**kwargs).read_why == _digest().read_why
    else:
        with pytest.raises(ValueError, match="unsupported fields"):
            map_reduce_digest(**kwargs)
    assert len(generator.prompts) == 2 and mapper.calls == 2
    for prompt in mapper.verification_prompts:
        evidence = prompt.split("Paper text (numbered verbatim passages):", 1)[1]
        evidence = evidence.split("Reader goals", 1)[0]
        assert PAPER in evidence and NO_ACCESS not in evidence


@pytest.mark.parametrize("source", ["", " \n\t"])
def test_explicit_empty_verification_source_never_falls_back_to_generated_notes(source):
    generator = _Generator(_digest())
    with pytest.raises(ValueError, match="non-empty source text"):
        assess_digest(title="Paper", full_text=PAPER, verification_text=source,
                      config=_default_goals_config(), llm=generator, verifier_llm=_Verifier())
    assert generator.prompts == []


@pytest.mark.parametrize("failure", ["empty", "exception"])
def test_parallel_mapping_failure_stops_before_reduction(failure):
    class FailingMapper(_Mapper):
        def prompt(self, prompt):
            if "FAIL_CHUNK" in prompt:
                if failure == "exception":
                    raise RuntimeError("scripted mapper failure")
                return ""
            return PAPER

    source = "GOOD_CHUNK " + "x" * 500 + " FAIL_CHUNK " + "y" * 500
    mapper, generator = FailingMapper(PAPER), _Generator(_digest())
    error = RuntimeError if failure == "exception" else ValueError
    message = "scripted mapper failure" if failure == "exception" else "empty chunk summary"
    with pytest.raises(error, match=message):
        map_reduce_digest("Paper", source, _default_goals_config(),
                          map_llm=mapper, reduce_llm=generator,
                          chunk_chars=600, sub_concurrency=2)
    assert generator.prompts == [] and mapper.calls == 0


@pytest.mark.parametrize("strategy", ["rank", "prefix"])
def test_strategy_dispatch_verifies_only_real_selected_source(strategy):
    from zotero_summarizer.services.faithbench._corpus import chunk_text
    from zotero_summarizer.services.library._map_reduce import ChunkBudget, digest_for_strategy
    from zotero_summarizer.services.library._prompt_security import untrusted_input
    from zotero_summarizer.services.library._review_text import select_review_text

    source = ("Opening background prose. " * 200
              + "Methodology study design evaluation results findings performance. " * 100
              + " UNSELECTED_TAIL")
    cap = 700
    selected = source[:cap] if strategy == "prefix" else select_review_text([], source, budget=cap)
    assert len(source) > cap and "UNSELECTED_TAIL" not in selected
    if strategy == "rank":
        assert selected != source[:cap]
    config = _default_goals_config()
    config.quality_review.chunk_strategy = strategy
    mapper = _Mapper("unused map note")
    generator = _Generator(_digest("The paper presents a qualitative discussion."))
    result = digest_for_strategy("Paper", source, config, map_llm=mapper,
                                 reduce_llm=generator, budget=ChunkBudget(cap, 8000, 2))
    assert result.basis == "full_text" and mapper.map_calls == 0
    assert len(mapper.verification_prompts) == 1
    evidence = mapper.verification_prompts[0].split(
        "Paper text (numbered verbatim passages):", 1)[1].split("Reader goals", 1)[0].strip()
    expected = "\n\n".join(f"PASSAGE {i}:\n{untrusted_input(p)}"
                            for i, p in enumerate(chunk_text(selected)))
    assert evidence == expected
    assert "UNSELECTED_TAIL" not in evidence


@pytest.mark.parametrize("corrected", [False, True], ids=["initial", "corrected"])
def test_map_reduce_malformed_verifier_fallback_keeps_original_source(corrected):
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library._digest_verification import _Checks

    class MalformedMapper(_Mapper):
        def pydantic_prompt(self, *, prompt, **kwargs):
            self.verification_prompts.append(prompt)
            return {"checks": []}

    class FallbackGenerator(_Generator):
        def __init__(self, *digests):
            super().__init__(*digests)
            self.verification_prompts = []

        def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
            if pydantic_model is PaperDigest:
                return super().pydantic_prompt(prompt=prompt, pydantic_model=pydantic_model, **kwargs)
            assert pydantic_model is _Checks
            self.verification_prompts.append(prompt)
            checks = _AccessVerifier()._checks(prompt)
            return pydantic_model(checks=checks)

    good = _digest()
    bad = good.model_copy(update={"read_why": NO_ACCESS})
    mapper = MalformedMapper(NO_ACCESS)
    generator = FallbackGenerator(bad, good) if corrected else FallbackGenerator(good)
    result = map_reduce_digest("Paper", PAPER, _default_goals_config(),
                               map_llm=mapper, reduce_llm=generator)
    assert result.read_why == good.read_why and result.basis == "map_reduce"
    pathways = 2 if corrected else 1
    assert len(generator.prompts) == pathways
    assert len(mapper.verification_prompts) == 2 * pathways
    assert len(generator.verification_prompts) == pathways
    for prompt in mapper.verification_prompts + generator.verification_prompts:
        evidence = prompt.split("Paper text (numbered verbatim passages):", 1)[1]
        evidence = evidence.split("Reader goals", 1)[0]
        assert PAPER in evidence and NO_ACCESS not in evidence
    if corrected:
        assert "failed source verification" in generator.prompts[1]


def test_prefix_audit_retains_clipped_repeated_occurrence_and_partial_scope():
    source = "  " + PAPER + "\n\n" + PAPER + " UNSELECTED_TAIL"
    cap = 2 + len(PAPER) + 12
    generator, verifier, audit = _Generator(_digest()), _Verifier(), {}

    assess_digest(title="Paper", full_text=source, config=_default_goals_config(),
                  llm=generator, verifier_llm=verifier, prefix=True, max_chars=cap,
                  verification_audit=audit)

    assert audit["source"]["scope"] == "selected_original"
    assert audit["source"]["coverage"] == "partial"
    assert audit["source"]["selected_characters"] == cap
    passage = audit["passages"][0]
    assert passage["text"] == source[:cap].strip()
    passage_end = len(source[:cap].rstrip())
    assert passage["selected_span"] == {"start": 2, "end": passage_end}
    assert passage["original_fragments"] == [{
        "selected_span": {"start": 2, "end": passage_end},
        "original_span": {"start": 2, "end": passage_end}, "mapping": "exact",
    }]
    assert passage["pdf_page"] is None
    assert passage["page_mapping_reason"] == "text_extraction_has_no_page_mapping"
    assert len(generator.prompts) == 1 and verifier.calls == 1


def test_map_audit_preserves_correction_lineage_and_original_evidence():
    class CorrectingMapper(_Mapper):
        def _checks(self, prompt):
            checks = super()._checks(prompt)
            if self.calls == 1:
                checks[0].update(supported=False, evidence=[])
            return checks

    good = _digest()
    bad = _digest("An initial generated claim.")
    mapper, generator, audit = CorrectingMapper("Generated summary, not original evidence."), _Generator(bad, good), {}

    result = map_reduce_digest("Paper", PAPER, _default_goals_config(), map_llm=mapper,
                               reduce_llm=generator, verification_audit=audit)

    assert result.tldr == good.tldr and result.basis == "map_reduce"
    assert audit["source"]["scope"] == "complete_extracted_text"
    assert audit["passages"][0]["text"] == PAPER
    assert [attempt["outcome"] for attempt in audit["lineage"]] == ["support_not_established", "accepted"]
    assert audit["lineage"][0]["digest_sha256"] != audit["lineage"][1]["digest_sha256"]
    assert len(generator.prompts) == 2 and mapper.calls == 2 and mapper.map_calls == 1


def test_rank_audit_tracks_nonfirst_clipped_occurrence_document_order_and_synthetic_join(monkeypatch):
    from zotero_summarizer.services.faithbench import _corpus

    chunks = [PAPER + " First occurrence.", "Unselected bridge.",
              PAPER + " Nonfirst occurrence.", "Rank winner: methodology evaluation results."]
    source = "\n\n".join(chunks)
    spans, cursor = [], 0
    for chunk in chunks:
        spans.append((cursor, cursor + len(chunk)))
        cursor += len(chunk) + 2

    class RankedIndex:
        def __init__(self, text):
            assert text == source
            self.chunks, self.spans = chunks, spans

        def top_chunks(self, query, k):
            return [chunks[3], chunks[2]]  # relevance order differs from document order

    monkeypatch.setattr(_corpus, "PaperChunkIndex", RankedIndex)
    clipped = 32
    expected = chunks[2][:clipped] + "\n\n" + chunks[3]
    generator, verifier, audit = _Generator(_digest("The paper discusses methodology.")), _Verifier(), {}
    assess_digest(title="Paper", full_text=source, config=_default_goals_config(),
                  llm=generator, verifier_llm=verifier, max_chars=len(expected), verification_audit=audit)

    passage = audit["passages"][0]
    assert passage["text"] == expected
    assert passage["original_fragments"] == [
        {"selected_span": {"start": 0, "end": clipped},
         "original_span": {"start": spans[2][0], "end": spans[2][0] + clipped}, "mapping": "exact"},
        {"selected_span": {"start": clipped, "end": clipped + 2}, "original_span": None,
         "mapping": "unknown", "reason": "synthetic_separator"},
        {"selected_span": {"start": clipped + 2, "end": len(expected)},
         "original_span": {"start": spans[3][0], "end": spans[3][1]}, "mapping": "exact"},
    ]
    assert spans[2][0] != source.find(chunks[2][:clipped]) == 0
    assert passage["pdf_page"] is None and audit["source"]["coverage"] == "partial"
    assert len(generator.prompts) == 1 and verifier.calls == 1


class _MalformedAuditMapper(_Mapper):
    def pydantic_prompt(self, *, prompt, **kwargs):
        self.verification_prompts.append(prompt)
        return {"checks": []}


class _FallbackAuditGenerator(_Generator):
    def __init__(self, *digests, unavailable=False):
        super().__init__(*digests)
        self.verification_prompts, self.unavailable = [], unavailable

    def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
        from zotero_summarizer.models import PaperDigest
        from zotero_summarizer.services.library._digest_verification import _Checks

        if pydantic_model is PaperDigest:
            return super().pydantic_prompt(prompt=prompt, pydantic_model=pydantic_model, **kwargs)
        assert pydantic_model is _Checks
        self.verification_prompts.append(prompt)
        return "" if self.unavailable else pydantic_model(checks=_AccessVerifier()._checks(prompt))


@pytest.mark.parametrize("corrected", [False, True], ids=["initial", "corrected"])
def test_fallback_audit_retains_unavailability_route_accepted_checks_and_original_scope(corrected):
    good, bad = _digest(), _digest().model_copy(update={"read_why": NO_ACCESS})
    mapper = _MalformedAuditMapper(NO_ACCESS)
    generator = _FallbackAuditGenerator(bad, good) if corrected else _FallbackAuditGenerator(good)
    audit = {}
    result = map_reduce_digest("Paper", PAPER, _default_goals_config(), map_llm=mapper,
                               reduce_llm=generator, verification_audit=audit)
    attempts = 2 if corrected else 1
    assert result.tldr == good.tldr
    assert [step["outcome"] for step in audit["lineage"]] == (["support_not_established"] if corrected else []) + ["accepted"]
    assert all(step["verifier_route"] == "generator_fallback" and step["unavailable_routes"] == [
        {"route": "primary", "reason": "malformed_verifier_output"}] for step in audit["lineage"])
    assert audit["passages"][0]["text"] == PAPER and audit["source"]["scope"] == "complete_extracted_text"
    assert all(claim["evidence"] == [0] for claim in audit["claims"])
    assert audit["contradiction_adjudication"] == "not_performed"
    assert len(mapper.verification_prompts) == 2 * attempts
    assert len(generator.verification_prompts) == len(generator.prompts) == attempts


def test_audit_unavailable_fallback_does_not_publish_or_regenerate():
    from zotero_summarizer.services.library._digest_verification import DigestVerifierUnavailable

    mapper, generator = _MalformedAuditMapper(PAPER), _FallbackAuditGenerator(_digest(), unavailable=True)
    audit = {"old_accepted_evidence": True}
    with pytest.raises(DigestVerifierUnavailable):
        map_reduce_digest("Paper", PAPER, _default_goals_config(), map_llm=mapper,
                           reduce_llm=generator, verification_audit=audit)
    assert audit == {}
    assert len(mapper.verification_prompts) == len(generator.verification_prompts) == 2
    assert len(generator.prompts) == 1
