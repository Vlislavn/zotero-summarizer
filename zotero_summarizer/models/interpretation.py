"""Optional scientific content; provenance and acceptance are caller-owned."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, StringConstraints

from zotero_summarizer.models import PaperDigest

Sentence = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=512)]


class InterpretationItem(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    finding_index: Annotated[int, Field(ge=0, le=4)]
    finding: Sentence
    basis_refs: list[Literal['methods', 'limitations', 'parameters']] = Field(min_length=1, max_length=3)
    studied_context: Sentence
    inference_strength: Literal['descriptive', 'association', 'causal', 'not_established']
    rationale: Sentence
    uncertainties: list[Sentence] = Field(max_length=3)
    unsupported_extrapolations: list[Sentence] = Field(max_length=3)


class InterpretationDigest(PaperDigest):
    interpretation: SkipValidation[Annotated[list[InterpretationItem] | None, Field(max_length=5)]] = None
