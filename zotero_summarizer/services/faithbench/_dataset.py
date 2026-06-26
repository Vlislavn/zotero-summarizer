"""Frozen benchmark file: schemas, JSONL persistence, versioning, review CSV.

A benchmark is one ``benchmark_v<N>.jsonl``: a ``meta`` header line (paper
manifest + build config) followed by one line per item, discriminated by
``kind`` (``qa`` | ``trap``). Once written, a benchmark file is immutable —
a rebuild produces ``v<N+1>``. The review CSV is the human escape hatch for
spot-checking auto-generated ground truth.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable, Literal, Union

from pydantic import BaseModel, Field

_BENCHMARK_RE = re.compile(r"^benchmark_v(\d+)\.jsonl$")


class PaperManifestEntry(BaseModel):
    item_key: str
    title: str
    text_sha256: str
    n_chars: int


class BenchmarkMeta(BaseModel):
    kind: Literal["meta"] = "meta"
    version: int
    created_at: str
    builder_model: str
    git_commit: str = ""
    papers: list[PaperManifestEntry]
    config: dict[str, Any] = Field(default_factory=dict)

    def paper_by_key(self, item_key: str) -> PaperManifestEntry:
        for p in self.papers:
            if p.item_key == item_key:
                return p
        raise KeyError(f"paper {item_key!r} not in benchmark manifest")


class QAItem(BaseModel):
    """An extractive QA pair whose gold answer is a verified verbatim span."""

    kind: Literal["qa"] = "qa"
    item_id: str                      # "qa:<paper_key>:<n>"
    paper_item_key: str
    paper_title: str
    paper_text_sha256: str
    question: str
    gold_answer: str                  # verbatim contiguous span from the frozen text
    span_start: int
    span_end: int
    answer_type: Literal["number", "entity", "span"] = "span"
    occurrences_in_paper: int = 1
    evidence_sentence: str = ""


class TrapItem(BaseModel):
    """An unanswerable question: generated from *another* paper, asked against
    this one. Correct behavior is abstention (``gold_behavior``)."""

    kind: Literal["trap"] = "trap"
    item_id: str                      # "trap:<paper_key>:<n>"
    paper_item_key: str               # the paper the question is asked AGAINST
    paper_title: str
    paper_text_sha256: str
    question: str
    source_paper_item_key: str        # where the question really came from
    source_gold_answer: str           # reviewer's eyes only
    gold_behavior: Literal["abstain"] = "abstain"


BenchmarkItem = Union[QAItem, TrapItem]


# ---------------------------------------------------------------------------
# Versioned persistence
# ---------------------------------------------------------------------------


def benchmark_path(faithbench_dir: Path, version: int) -> Path:
    return faithbench_dir / f"benchmark_v{version}.jsonl"


def existing_versions(faithbench_dir: Path) -> list[int]:
    if not faithbench_dir.exists():
        return []
    versions = []
    for entry in faithbench_dir.iterdir():
        match = _BENCHMARK_RE.match(entry.name)
        if match:
            versions.append(int(match.group(1)))
    return sorted(versions)


def next_benchmark_version(faithbench_dir: Path) -> int:
    versions = existing_versions(faithbench_dir)
    return (versions[-1] + 1) if versions else 1


def latest_benchmark_path(faithbench_dir: Path) -> Path:
    versions = existing_versions(faithbench_dir)
    if not versions:
        raise FileNotFoundError(
            f"no benchmark_v*.jsonl under {faithbench_dir}; run `faithbench build` first"
        )
    return benchmark_path(faithbench_dir, versions[-1])


def save_benchmark(path: Path, meta: BenchmarkMeta, items: Iterable[BenchmarkItem]) -> int:
    """Write the meta header + items; refuses to overwrite (immutability)."""
    if path.exists():
        raise FileExistsError(f"benchmark file already exists: {path} (benchmarks are immutable)")
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(meta.model_dump(), ensure_ascii=False) + "\n")
        for item in items:
            f.write(json.dumps(item.model_dump(), ensure_ascii=False) + "\n")
            count += 1
    return count


def load_benchmark(path: Path) -> tuple[BenchmarkMeta, list[BenchmarkItem]]:
    """Parse a benchmark file. Malformed lines are an error — the benchmark is
    a frozen artifact this code wrote; corruption must surface, not be skipped."""
    meta: BenchmarkMeta | None = None
    items: list[BenchmarkItem] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            kind = payload.get("kind")
            if kind == "meta":
                meta = BenchmarkMeta.model_validate(payload)
            elif kind == "qa":
                items.append(QAItem.model_validate(payload))
            elif kind == "trap":
                items.append(TrapItem.model_validate(payload))
            else:
                raise ValueError(f"{path}:{line_no}: unknown benchmark row kind {kind!r}")
    if meta is None:
        raise ValueError(f"{path}: missing meta header line")
    return meta, items


def items_by_id(items: list[BenchmarkItem]) -> dict[str, BenchmarkItem]:
    return {item.item_id: item for item in items}


# ---------------------------------------------------------------------------
# Reviewable CSV export
# ---------------------------------------------------------------------------

_REVIEW_FIELDS = [
    "item_id", "kind", "paper_title", "question", "gold_answer", "answer_type",
    "occurrences_in_paper", "span_context", "source_paper_item_key",
]


def export_review_csv(
    csv_path: Path, items: list[BenchmarkItem], paper_texts: dict[str, str]
) -> None:
    """Flat, human-reviewable export with ±200 chars of span context."""
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_REVIEW_FIELDS)
        writer.writeheader()
        for item in items:
            row: dict[str, Any] = {
                "item_id": item.item_id,
                "kind": item.kind,
                "paper_title": item.paper_title,
                "question": item.question,
            }
            if isinstance(item, QAItem):
                text = paper_texts.get(item.paper_item_key, "")
                lo = max(0, item.span_start - 200)
                hi = min(len(text), item.span_end + 200)
                row.update(
                    gold_answer=item.gold_answer,
                    answer_type=item.answer_type,
                    occurrences_in_paper=item.occurrences_in_paper,
                    span_context=text[lo:hi].replace("\n", " "),
                    source_paper_item_key="",
                )
            else:
                row.update(
                    gold_answer=item.source_gold_answer,
                    answer_type="",
                    occurrences_in_paper="",
                    span_context="(unanswerable by design — answer lives in source paper)",
                    source_paper_item_key=item.source_paper_item_key,
                )
            writer.writerow(row)
