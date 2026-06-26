"""Typed row models for the storage boundary.

The repository functions historically return ``dict[str, Any]`` straight from
SQLite, so a renamed/dropped column surfaces only as a silent ``None`` deep in a
service. These models put one strict, typed definition of a row in one place:
``from_row`` raises immediately if an expected column is missing (fail-loud on
schema drift), and ``to_dict`` reproduces the exact legacy key set so existing
callers are untouched.

This is the pattern for typing the boundary incrementally — add a model here and
route the matching reader through it. ``PendingChangeRow`` is the first adopter.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


def _require(row: Mapping[str, Any], columns: tuple[str, ...]) -> None:
    missing = [c for c in columns if c not in row]
    if missing:
        raise KeyError(
            f"row is missing expected column(s) {missing}; "
            f"got keys {sorted(row.keys())} — storage schema drift?"
        )


@dataclass(frozen=True)
class PendingChangeRow:
    """One row of the ``pending_changes`` table.

    ``payload_json`` is kept as the stored value (a JSON string from the DB) so
    the legacy dict contract is byte-for-byte preserved; use :attr:`payload` for
    the parsed object.
    """

    id: int
    item_key: str
    item_title: str
    change_type: str
    payload_json: str
    status: str
    error_message: str | None
    created_at: str
    applied_at: str | None

    _COLUMNS = (
        "id",
        "item_key",
        "item_title",
        "change_type",
        "payload_json",
        "status",
        "error_message",
        "created_at",
        "applied_at",
    )

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "PendingChangeRow":
        _require(row, cls._COLUMNS)
        return cls(
            id=int(row["id"]),
            item_key=str(row["item_key"]),
            item_title=str(row["item_title"] or ""),
            change_type=str(row["change_type"]),
            payload_json=row["payload_json"],
            status=str(row["status"]),
            error_message=row["error_message"],
            created_at=str(row["created_at"]),
            applied_at=row["applied_at"],
        )

    @property
    def payload(self) -> dict[str, Any]:
        if isinstance(self.payload_json, dict):
            return self.payload_json
        if not self.payload_json:
            return {}
        decoded = json.loads(self.payload_json)
        return decoded if isinstance(decoded, dict) else {}

    def to_dict(self) -> dict[str, Any]:
        return {column: getattr(self, column) for column in self._COLUMNS}
