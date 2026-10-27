"""Compact generation work is separate from the broader immutable report contract."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

TOPIC_OUTPUT_ALLOWANCE = 1024
MAX_GENERATED_TOPICS = 6
CATEGORIES = ("context", "news", "discussion", "contrasts", "evidence", "caveats", "implications")


class TelegramMiniCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_id: str = Field(min_length=1)


class TelegramMiniAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=320)
    citations: list[TelegramMiniCitation] = Field(min_length=1, max_length=2)


class TelegramMiniTopic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=160)
    context: TelegramMiniAssertion
    news: list[TelegramMiniAssertion] = Field(max_length=1)
    discussion: list[TelegramMiniAssertion] = Field(max_length=1)
    contrasts: list[TelegramMiniAssertion] = Field(max_length=1)
    evidence: list[TelegramMiniAssertion] = Field(max_length=1)
    caveats: list[TelegramMiniAssertion] = Field(min_length=1, max_length=1)
    implications: list[TelegramMiniAssertion] = Field(max_length=1)


def _scoped_assertion(name: str, reference_ids: tuple[str, ...]):
    citation = create_model(name + "Citation", __base__=TelegramMiniCitation,
                            evidence_id=(Literal[reference_ids], Field()))
    return create_model(name + "Assertion", __base__=TelegramMiniAssertion,
                        citations=(list[citation], Field(min_length=1, max_length=2)))


def generation_schema(output_tokens: int, evidence_roles: dict[str, str]):
    all_ids = tuple(sorted(evidence_roles))
    if not all_ids:
        raise ValueError("Telegram generation requires admitted sources")
    any_source = _scoped_assertion("TelegramMini", all_ids)
    fields = {"context": (any_source, Field())}
    for category in CATEGORIES[1:]:
        ids = tuple(sorted(key for key, role in evidence_roles.items() if role == category))
        scoped = (_scoped_assertion("TelegramMini" + category.capitalize(), ids)
                  if ids and ids != all_ids else any_source)
        available = bool(ids) if category in {"news", "discussion"} else True
        fields[category] = (list[scoped], Field(min_length=1 if category == "caveats" else 0,
                                               max_length=1 if available else 0))
    topic = create_model("TelegramMiniTopic", __base__=TelegramMiniTopic, **fields)
    limit = max(1, min(MAX_GENERATED_TOPICS, output_tokens // TOPIC_OUTPUT_ALLOWANCE))
    return create_model(
        "TelegramMiniReviews", __config__=ConfigDict(extra="forbid", strict=True),
        topics=(list[topic], Field(min_length=1, max_length=limit)),
    )


def expand_topics(value, schema, evidence: dict[str, dict]) -> dict:
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    topics = schema.model_validate(raw).model_dump(mode="json")["topics"]
    for topic in topics:
        assertions = [topic["context"], *[claim for name in CATEGORIES[1:] for claim in topic[name]]]
        for claim in assertions:
            claim["citations"] = [{"source_id": evidence[cite["evidence_id"]]["source_id"],
                                   "quote": evidence[cite["evidence_id"]]["quote"]}
                                  for cite in claim["citations"]]
        topic["source_ids"] = sorted({cite["source_id"] for claim in assertions for cite in claim["citations"]})
    return {"topics": topics}
