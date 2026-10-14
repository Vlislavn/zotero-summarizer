"""Experimental producer-only interpretation shapes, not scientific certification.

Import directly only for an explicit C collector; PaperDigest remains unchanged.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel, ConfigDict, Field, ModelWrapValidatorHandler, StrictStr,
    ValidationError, ValidatorFunctionWrapHandler, field_validator, model_validator,
)

from zotero_summarizer.models.triage import PaperDigest


BasisRef = Annotated[StrictStr, Field(pattern=(
    r"^/(?:methods|limitations|key_weakness|parameters(?:/(?:dataset|baselines|"
    r"sample_size|metrics|architecture|external_validation))?)$"
))]


class InterpretationItem(BaseModel):
    """Scientific text qualifies a referenced finding; it never copies that finding."""

    model_config = ConfigDict(extra="forbid", strict=True)
    finding_ref: StrictStr = Field(pattern=r"^/(?:tldr|key_findings/(?:0|[1-9][0-9]*))$")
    inference_strength: StrictStr
    rationale: StrictStr
    scope: StrictStr
    uncertainties: list[StrictStr]
    unsupported_extrapolations: list[StrictStr]
    basis_refs: list[BasisRef] = Field(description=(
        "Existing /methods, /limitations, /key_weakness, /parameters or "
        "/parameters/<field> pointers; no copied basis text or evidence authority."
    ))


class InterpretationDraft(BaseModel):
    """Typed AI content only; origin and verification state belong to the service."""

    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[InterpretationItem] = Field(min_length=1)


class InterpretationUnavailable(BaseModel):
    """Explicit optional-content failure, never a support or contradiction verdict."""

    model_config = ConfigDict(extra="forbid", strict=True)
    unavailable_reason: Literal[
        "missing_interpretation", "malformed_interpretation", "invalid_references",
    ]


class InterpretationDigestResponse(PaperDigest):
    """C-only response schema; malformed optional content preserves a valid base."""

    interpretation: InterpretationDraft | InterpretationUnavailable | None = Field(
        default=None, validate_default=True,
    )

    @field_validator("interpretation", mode="wrap")
    @classmethod
    def _optional_content(
        cls, value: Any, handler: ValidatorFunctionWrapHandler,
    ) -> InterpretationDraft | InterpretationUnavailable:
        if value is None:
            return InterpretationUnavailable(unavailable_reason="missing_interpretation")
        try:
            return handler(value)
        except ValidationError:
            return InterpretationUnavailable(unavailable_reason="malformed_interpretation")

    @model_validator(mode="wrap")
    @classmethod
    def _preserve_presence(
        cls, value: Any, handler: ModelWrapValidatorHandler["InterpretationDigestResponse"],
    ) -> "InterpretationDigestResponse":
        response = handler(value)
        if isinstance(value, dict):
            # Inherited skip normalization writes defaults; omission is still omission.
            response.model_fields_set.intersection_update(value)
        return response


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, BaseModel):
        return _has_value(value.model_dump())
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    return value is not None


def _basis_value(digest: PaperDigest, ref: str) -> Any:
    parts = ref.removeprefix("/").split("/")
    value = getattr(digest, parts[0])
    return getattr(value, parts[1]) if len(parts) == 2 and value is not None else value


def interpretation_payload(
    content: InterpretationDraft | InterpretationUnavailable, digest: PaperDigest,
) -> dict[str, Any]:
    """Bind pointer shape/current content only, without asserting entailment."""
    state = {"origin": "ai_generated", "scientific_verification": "unverified"}
    if isinstance(content, InterpretationDraft):
        findings = {"/tldr": digest.tldr}
        findings.update((f"/key_findings/{index}", finding) for index, finding in enumerate(digest.key_findings))
        seen: set[str] = set()
        for item in content.items:
            ref = item.finding_ref
            if ref in seen or not findings.get(ref, "").strip() or any(
                not _has_value(_basis_value(digest, basis)) for basis in item.basis_refs
            ):
                content = InterpretationUnavailable(unavailable_reason="invalid_references")
                break
            seen.add(ref)
    if isinstance(content, InterpretationUnavailable):
        return {**state, "status": "unavailable", **content.model_dump(mode="json")}
    return {**state, "status": "schema_validated_ai_draft", **content.model_dump(mode="json")}
