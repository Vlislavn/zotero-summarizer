import re

import pytest

from zotero_summarizer.models import PaperDigest
from zotero_summarizer.services.library._digest_verification import _claims, _unsupported_literals, verify_digest
from zotero_summarizer.services.library.quality_review import assess_digest
from zotero_summarizer.services.setup.bootstrap import _default_goals_config


PAPER = "The paper reports a carefully controlled evaluation on ImageNet using held-out data."


class _Verifier:
    def __init__(self, supported=True, duplicate=False):
        self.supported = supported
        self.duplicate = duplicate
        self.calls = 0

    def pydantic_prompt(self, *, prompt, pydantic_model, **kwargs):
        self.calls += 1
        return pydantic_model(checks=self._checks(prompt))

    def _checks(self, prompt):
        count = max(map(int, re.findall(r"\[(\d+)\]", prompt)), default=-1) + 1
        checks = [
            {"index": i, "supported": self.supported, "quote": PAPER} for i in range(count)
        ]
        return checks + checks[:1] if self.duplicate else checks


class _EmptyThenValidVerifier(_Verifier):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def pydantic_prompt(self, **kwargs):
        self.calls += 1
        return "" if self.calls == 1 else {"checks": self._checks(kwargs["prompt"])}

def _digest(tldr="The paper evaluates a model on ImageNet."):
    return PaperDigest(
        tldr=tldr, read_decision="skip", read_why="The digest is sufficient.",
        read_parts=[], skip_parts=[], estimated_read_minutes=None, original_value="",
        writing_friction="low", writing_reasons=[],
    ).model_copy(update={"basis": "full_text"})


def test_digest_verification_rejects_missing_literals_and_unsupported_claims():
    with pytest.raises(ValueError, match="source-absent literals"):
        verify_digest(_digest("The model reaches 99% on SecretSet."), PAPER, _Verifier())
    with pytest.raises(ValueError, match="unsupported fields"):
        verify_digest(_digest(), PAPER, _Verifier(supported=False))


def test_digest_verification_rejects_duplicate_checks():
    with pytest.raises(ValueError, match="omitted or duplicated"):
        verify_digest(_digest(), PAPER, _Verifier(duplicate=True))


def test_literal_guard_handles_ranges_without_inventing_negative_numbers():
    assert not _unsupported_literals(["tldr: discovery took 30-45 minutes"], "It took 30-45 minutes.")
    assert not _unsupported_literals(["finding: 43.5-54.6%"], "The range was 43.5%–54.6%.")
    assert not _unsupported_literals(["implementation[0]: no numeric claim"], "paper text")


def test_verifier_checks_claim_fields_not_reviewer_metadata():
    fields = {claim.partition(":")[0] for claim in _claims(_digest())}
    assert "tldr" in fields
    assert "confidence" not in fields
    digest = _digest().model_copy(update={"key_findings": ["First fact; second fact.", "Third fact."]})
    assert len([claim for claim in _claims(digest) if claim.startswith("key_findings")]) == 3


def test_digest_verifier_retries_one_malformed_response():
    llm = _EmptyThenValidVerifier()
    verify_digest(_digest(), PAPER, llm)
    assert llm.calls == 2


def test_digest_generation_uses_separate_verifier_client():
    class Generator:
        def pydantic_prompt(self, **_kwargs):
            return _digest()

    verifier = _Verifier()
    assess_digest(
        title="Paper", full_text=PAPER, config=_default_goals_config(),
        llm=Generator(), verifier_llm=verifier,
    )
    assert verifier.calls == 1


def test_digest_is_corrected_once_after_verifier_rejection(monkeypatch):
    from zotero_summarizer.services.library import _digest_verification

    class Generator:
        def __init__(self):
            self.calls = 0

        def pydantic_prompt(self, **_kwargs):
            self.calls += 1
            return _digest("unsupported" if self.calls == 1 else "corrected")

    checks = []

    def verify(digest, *_args, **_kwargs):
        checks.append(digest.tldr)
        if len(checks) == 1:
            raise ValueError("unsupported field")

    monkeypatch.setattr(_digest_verification, "verify_digest", verify)
    generator = Generator()
    result = assess_digest(
        title="Paper", full_text=PAPER, config=_default_goals_config(), llm=generator,
    )
    assert result.tldr == "corrected"
    assert generator.calls == 2 and checks == ["unsupported", "corrected"]


def test_unavailable_light_verifier_falls_back_to_generator(monkeypatch):
    from zotero_summarizer.services.library import _digest_verification

    class Generator:
        def pydantic_prompt(self, **_kwargs):
            return _digest()

    generator, light, calls = Generator(), object(), []

    def verify(_digest, _text, llm, **_kwargs):
        calls.append(llm)
        if llm is light:
            raise _digest_verification.DigestVerifierUnavailable("invalid JSON")

    monkeypatch.setattr(_digest_verification, "verify_digest", verify)
    assess_digest(
        title="Paper", full_text=PAPER, config=_default_goals_config(),
        llm=generator, verifier_llm=light,
    )
    assert calls == [light, generator]
