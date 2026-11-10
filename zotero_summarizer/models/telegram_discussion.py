"""Additive conversation snapshots and source-bound, unverified topic reviews."""
from __future__ import annotations

import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zotero_summarizer.models.research_feed import (
    LiveTelegramChannelId, TelegramPost, TelegramTarget, TelegramModelSelection, TelegramLimits,
)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


class TelegramMessage(BaseModel):
    """Numeric sender id only (no names, account identity or transient SDK access hashes)."""

    model_config = ConfigDict(extra="forbid", strict=True)
    peer_kind: Literal["channel"] = "channel"
    peer_id: LiveTelegramChannelId
    message_id: int = Field(ge=1, le=2**31 - 1)
    thread_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    reply_to_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    reply_to_top_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    reply_peer_is_current: bool | None = None
    sender_id: int | None = None  # the author's Telegram user id (captured from schema 2 on; no names)
    text: str
    published_at: str
    updated_at: str | None = None
    permalink: str | None = None
    urls: list[str] = Field(default_factory=list)
    skip_reason: str | None = None

    @field_validator("published_at", "updated_at")
    @classmethod
    def _date(cls, value):
        return TelegramPost._aware_date(value)

    @model_validator(mode="after")
    def _original(self):
        if not self.text.strip() and not self.skip_reason:
            raise ValueError("empty messages require skip_reason")
        for value in [*self.urls, *([self.permalink] if self.permalink else [])]:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("message URLs require absolute HTTP(S)")
        return self

    @property
    def source_id(self) -> str:
        return f"telegram:{self.peer_id}:{self.message_id}"

    @property
    def content_hash(self) -> str:
        value = self.model_dump(mode="json")
        if self.reply_peer_is_current is None:
            value.pop("reply_peer_is_current")
        if self.sender_id is None:
            value.pop("sender_id")
        return digest(value)


class TelegramPostWindow(BaseModel):
    """Observed channel-post selection, independent of per-post reply coverage."""

    model_config = ConfigDict(extra="forbid", strict=True)
    limit: int = Field(ge=1, le=3)
    scanned: int = Field(ge=0, le=4)
    retained: int = Field(ge=0, le=3)
    lookahead: int = Field(ge=0, le=1)
    truncated: bool

    @model_validator(mode="after")
    def _counts(self):
        if (self.retained != min(self.scanned, self.limit)
                or self.lookahead != self.scanned - self.retained
                or self.truncated != (self.lookahead > 0)):
            raise ValueError("invalid Telegram post-window coverage")
        return self


class TelegramConversation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: TelegramTarget
    origin: TelegramPost | None = None
    peer_id: LiveTelegramChannelId | None = None
    root_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    root: TelegramMessage | None = None
    messages: list[TelegramMessage] = Field(default_factory=list, max_length=300)
    status: Literal["fetched", "truncated", "unavailable", "empty"]
    reason: str | None = None
    scanned_count: int = Field(default=0, ge=0)
    text_count: int = Field(default=0, ge=0, le=300)
    skipped_count: int = Field(default=0, ge=0)
    reported_reply_count: int | None = Field(default=None, ge=0)
    post_window: TelegramPostWindow | None = None
    attempted: bool | None = None
    observed_at: str

    @field_validator("observed_at")
    @classmethod
    def _date(cls, value):
        return TelegramPost._aware_date(value)

    @model_validator(mode="after")
    def _binding(self):
        retained_texts = sum(bool(row.text.strip()) and not row.skip_reason for row in self.messages)
        if "text_count" in self.model_fields_set and self.text_count != retained_texts:
            raise ValueError("conversation retained text count differs")
        self.text_count = retained_texts
        ids = [row.message_id for row in self.messages]
        if ids != sorted(set(ids)):
            raise ValueError("conversation messages require unique ascending IDs")
        if any(row.peer_id != self.peer_id or row.thread_id != self.root_id for row in self.messages):
            raise ValueError("conversation peer/thread mismatch")
        if self.root is not None and (self.root.peer_id != self.peer_id or self.root.message_id != self.root_id):
            raise ValueError("conversation root mismatch")
        if self.status == "empty" and self.messages:
            raise ValueError("empty is an observed zero-reply result")
        if self.status == "fetched" and not self.messages:
            raise ValueError("fetched requires messages")
        if self.status == "unavailable" and not self.reason:
            raise ValueError("unavailable requires a reason")
        if self.origin is not None and self.target.kind != "broadcast":
            raise ValueError("news origin requires broadcast target")
        return self

    @property
    def content_hash(self) -> str:
        # Additive coverage must not invalidate already-published v2 identities.
        omitted = {name for name in ("post_window", "attempted") if getattr(self, name) is None}
        value = self.model_dump(mode="json", exclude={"observed_at", *omitted})
        for row in [*value["messages"], *([value["root"]] if value["root"] else [])]:
            if row.get("reply_peer_is_current") is None:
                row.pop("reply_peer_is_current", None)
        return digest(value)


class TelegramCollectionStopped(ValueError):
    """Completed/stopped observations survive a later scoped RPC refusal."""

    def __init__(self, failure, observations):
        self.failure = failure
        self.observations = observations
        super().__init__("Telegram collection stopped: source/root response unavailable")


class TelegramCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class TelegramAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=3000)
    citations: list[TelegramCitation] = Field(min_length=1, max_length=6)


class TelegramTopic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=120)
    context: TelegramAssertion
    news: list[TelegramAssertion] = Field(max_length=5)
    discussion: list[TelegramAssertion] = Field(max_length=8)
    contrasts: list[TelegramAssertion] = Field(max_length=5)
    evidence: list[TelegramAssertion] = Field(max_length=5)
    caveats: list[TelegramAssertion] = Field(min_length=1, max_length=5)
    implications: list[TelegramAssertion] = Field(max_length=5)


class TelegramTopics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    topics: list[TelegramTopic] = Field(max_length=6)


def source_records(conversations: list[TelegramConversation]) -> dict[str, dict]:
    records = {}
    mirrored_roots = {f"telegram:{batch.peer_id}:{batch.root_id}" for batch in conversations
                      if batch.origin is not None and batch.peer_id is not None and batch.root_id is not None}
    for batch in conversations:
        # A linked root is the forwarded news already retained in origin, not a
        # second participant corroborating it. Group/thread overlaps share evidence.
        roots = [batch.root] if batch.root and not batch.origin else []
        values = [(row.source_id, row.model_dump(mode="json", exclude={"thread_id"} | (
            {"reply_peer_is_current"} if row.reply_peer_is_current is None else set())), "discussion")
                  for row in [*roots, *batch.messages]]
        if batch.origin:
            values.insert(0, (batch.origin.source_id, batch.origin.model_dump(mode="json"), "news"))
        for source_id, value, role in values:
            if source_id in mirrored_roots:
                continue  # Known forwarded news cannot become a participant in another selected window.
            context = {"target": batch.target.key, "thread_id": batch.root_id}
            if source_id in records:
                record = records[source_id]
                if record["content_hash"] != digest(value) or record["role"] != role:
                    raise ValueError("conflicting Telegram source snapshots")
                if context not in record["contexts"]:
                    record["contexts"].append(context)
            else:
                records[source_id] = {"source_id": source_id, "role": role, "original": value,
                                      "content_hash": digest(value), "contexts": [context]}
    return records


__all__ = ["TelegramTarget", "TelegramModelSelection", "TelegramLimits", "TelegramMessage",
           "TelegramConversation", "TelegramPostWindow", "TelegramCollectionStopped", "TelegramTopics", "source_records", "digest"]
