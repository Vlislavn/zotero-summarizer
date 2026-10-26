"""Typed Pending approval snapshots and closed generated-note proposals."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zotero_summarizer.domain import READING_PRIORITY_VALUES

READING_PRIORITY_GLYPHS = {"must_read": "🔥", "should_read": "👀", "could_read": "📎", "dont_read": "—"}
VERDICT_NOTE_MARKER = "zs:note_type=verdict"


def build_verdict_note_html(user_priority: str, comment: str) -> str:
    """Canonical verdict payload, shared by generation and stored-effect admission."""
    glyph = READING_PRIORITY_GLYPHS.get(user_priority, "•")
    label = (user_priority or "").replace("_", " ").title() or "Verdict"
    body = html.escape((comment or "").strip())
    return (
        f"<!-- {VERDICT_NOTE_MARKER};version=1 -->"
        f"<h2>{html.escape(glyph)} {html.escape(label)}</h2>"
        f"<p>{body}</p>"
        f"<p><em>{VERDICT_NOTE_MARKER};version=1</em></p>"
    )


def preview_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


class ZoteroTargetIdentity(BaseModel):
    """Native target identity, independent of database contents and edit clocks."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    database_path: str = Field(min_length=1)
    device: int = Field(ge=0)
    inode: int = Field(gt=0)
    library_id: int = Field(ge=0)
    library_type: Literal["user"] = "user"


class CompositeNoteIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    lookup_item_key: str
    revision: int = Field(ge=0)
    body: str
    target_key: str

    @field_validator("lookup_item_key", "target_key")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Composite note identity must be nonblank")
        return value


class UserNotePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    marker: Literal["zs:note_type=user_note"] = "zs:note_type=user_note"
    note_title: Literal["My notes"] = "My notes"
    note_html: str

    @field_validator("note_html")
    @classmethod
    def owned_nonblank(cls, value: str) -> str:
        if not value.strip() or "zs:note_type=user_note" not in value:
            raise ValueError("Composite note payload lacks its ownership marker")
        return value


class VerdictNotePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    marker: Literal["zs:note_type=verdict"] = "zs:note_type=verdict"
    note_title: str
    note_html: str = Field(min_length=1)

    @classmethod
    def for_intent(cls, intent: VerdictMirrorIntent) -> VerdictNotePayload:
        priority = intent.value or ""
        comment = (intent.comment or "") if intent.value is not None else ""
        return cls(note_title=f"Verdict: {priority}", note_html=build_verdict_note_html(priority, comment))


class VerdictMirrorIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    lookup_item_key: str = Field(min_length=1)
    source_item_key: str = Field(min_length=1)
    revision: int = Field(ge=0)
    value: str | None
    comment: str | None
    verdict_source: str = Field(min_length=1)
    target_key: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def known_priority(cls, value):
        if value is not None and value not in READING_PRIORITY_VALUES:
            raise ValueError("Unknown mirrored reading priority")
        return value


class SystemMirrorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event: Literal["mirror_requested"] = "mirror_requested"
    actor: Literal["system"] = "system"
    source: Literal["mirror_user_note", "mirror_verdict"] = "mirror_user_note"
    requested: CompositeNoteIntent | VerdictMirrorIntent
    target: ZoteroTargetIdentity
    recovery: Literal["backup_only"] = "backup_only"
    backup_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def absolute_references(self) -> SystemMirrorRequest:
        if (self.source == "mirror_user_note") != isinstance(self.requested, CompositeNoteIntent):
            raise ValueError("System mirror source differs from its typed intent")
        native, backup = Path(self.target.database_path), Path(self.backup_path)
        if not native.is_absolute() or not backup.is_absolute() or backup.parent != native.parent:
            raise ValueError("Composite note references must share the absolute native directory")
        return self


class PendingApprovalPreview(BaseModel):
    """Exact queued effect and native target shown before explicit Apply."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1] = 1
    change_id: int = Field(gt=0)
    item_key: str
    change_type: str
    payload: dict[str, Any]
    edit_revision: int = Field(ge=0)
    target: ZoteroTargetIdentity
    generated_preview_sha256: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @field_validator("item_key", "change_type")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Pending identity must be nonblank")
        return value

    @property
    def preview_sha256(self) -> str:
        return preview_hash(self.model_dump())


class DigestNotePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    note_title: Literal["Deep digest"] = "Deep digest"
    marker: str
    note_html: str

    @field_validator("marker", "note_html")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Note payload must be nonempty")
        return value


class GeneratedNoteProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: Literal["deep_review"] = "deep_review"
    change_type: Literal["upsert_note"] = "upsert_note"
    item_key: str
    # None permits legacy display/rejection only; the proposal builder always binds a target.
    target: ZoteroTargetIdentity | None = None
    review_revision: str
    source_sha256: str
    body_sha256: str
    payload: DigestNotePayload

    @field_validator("review_revision")
    @classmethod
    def publication_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("Noncanonical publication revision")
        return value

    @field_validator("item_key", "source_sha256", "body_sha256")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Proposal identity must be nonempty")
        return value

    @model_validator(mode="after")
    def body_matches(self) -> GeneratedNoteProposal:
        if self.body_sha256 != hashlib.sha256(self.payload.note_html.encode("utf-8")).hexdigest():
            raise ValueError("Note body digest mismatch")
        return self

    @property
    def preview_sha256(self) -> str:
        return preview_hash(self.model_dump())
