"""Canonical JSON + compact Markdown rendering for a weekly research feed."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zotero_summarizer.services._common import write_json_atomic


def markdown(payload: dict[str, Any]) -> str:
    meta, cards = payload["metadata"], payload["cards"]
    lines = [
        f"# Research Intelligence · {meta['from']} → {meta['to']}", "",
        f"Discovered {meta['counts']['discovered']}; shortlisted {meta['counts']['shortlisted']}; "
        f"cards {meta['counts']['cards_generated']}.", "",
    ]
    for index, record in enumerate(cards, 1):
        candidate, card = record["candidate"], record["card"]
        lines += [
            f"## {index}. {candidate['title']}", "",
            f"**Action:** {card['worth_reading']} · **Projects:** "
            f"{', '.join(record['triage']['matched_projects']) or 'none'}", "",
            card["problem"], "",
            f"**Core idea:** {card['core_idea'] or 'not established'}", "",
            f"**Project use:** {'; '.join(card['project_use_notes'])}", "",
            f"**Artifacts:** {', '.join(card['code_urls'] + card['dataset_urls'] + card['model_urls']) or 'none verified'}", "",
        ]
    rejected = payload.get("rejected_near_threshold") or []
    if rejected:
        lines += ["## Near-threshold rejects", ""]
        lines += [f"- {row['candidate']['title']} — {row['triage']['rationale']}" for row in rejected]
        lines.append("")
    missing = payload.get("manual_full_text_required") or []
    if missing:
        lines += ["## Needs full text", ""] + [f"- {row['title']}" for row in missing] + [""]
    return "\n".join(lines)


def persist(payload: dict[str, Any], output_dir: Path, slug: str) -> tuple[Path, Path]:
    json_path = output_dir / f"{slug}.json"
    md_path = output_dir / f"{slug}.md"
    write_json_atomic(json_path, payload)
    md_path.write_text(markdown(payload), encoding="utf-8")
    return json_path, md_path


__all__ = ["markdown", "persist"]
