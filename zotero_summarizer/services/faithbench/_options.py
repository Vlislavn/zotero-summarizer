"""Pure execution options shared by CLI admission and the benchmark runner."""
from __future__ import annotations

from dataclasses import dataclass

CONDITIONS = ("full_text", "retrieval")
TRACKS = ("qa", "claims")


@dataclass(frozen=True)
class RunOptions:
    """Execution knobs for ``run_benchmark``, bundled so call sites don't have
    to spell out all of them; defaults match the previous individual kwargs."""

    conditions: tuple[str, ...] = CONDITIONS
    tracks: tuple[str, ...] = TRACKS
    runs: int = 1
    limit: int | None = None
    retry_errors: bool = False
    serial: bool = True
    max_workers: int = 4

    def __post_init__(self) -> None:
        for name, allowed in (("conditions", CONDITIONS), ("tracks", TRACKS)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values or any(v not in allowed for v in values):
                raise ValueError(f"{name} must be a nonempty tuple drawn from {allowed}")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
        for name in ("runs", "limit", "max_workers"):
            value = getattr(self, name)
            if name == "limit" and value is None:
                continue
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.limit is not None and "qa" not in self.tracks:
            raise ValueError("limit requires the qa track")
