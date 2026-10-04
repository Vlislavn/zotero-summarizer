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
