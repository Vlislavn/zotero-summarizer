"""Canonical JSON + compact Markdown rendering for a weekly research feed."""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Callable

from zotero_summarizer.services._common import write_json_atomic
from zotero_summarizer.services.research_feed.cursor import NOT_CACHED
from zotero_summarizer.services.research_feed.source import PLAIN_REASONS


def _project_use(notes: list[str]) -> str:
    """«a: S; b: S» → «a, b: S» — the same sentence is said once."""
    groups: dict[str, list[str]] = {}
    for note in notes:
        project, sep, sentence = note.partition(": ")
        groups.setdefault(sentence if sep else note, []).append(project if sep else "")
    return "; ".join(f"{', '.join(p for p in projects if p)}: {sentence}" if any(projects) else sentence
                     for sentence, projects in groups.items())


def _humanise(key: str) -> str:
    """«dataset_provenance» → «Dataset provenance»; real sentences pass through."""
    return key[:1].upper() + key[1:].replace("_", " ") if "_" in key and " " not in key else key


def _range_text(start: str, end: str) -> str:
    """«2026-10-14T00:00:00+00:00», «2026-10-20T23:59:…» → «14–20 Oct 2026» (as the Weekly page shows it)."""
    try:
        a, b = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
    except ValueError:
        return f"{start} → {end}"
    if a == b:
        return f"{a.day} {a:%b %Y}"
    if a.year != b.year:
        return f"{a.day} {a:%b %Y} – {b.day} {b:%b %Y}"
    if a.month != b.month:
        return f"{a.day} {a:%b} – {b.day} {b:%b %Y}"
    return f"{a.day}–{b.day} {b:%b %Y}"


def markdown(payload: dict[str, Any]) -> str:
    meta, cards = payload["metadata"], payload["cards"]
    lines = [
        f"# Research Intelligence · {_range_text(meta['from'], meta['to'])}", "",
        f"Discovered {meta['counts']['discovered']}; shortlisted {meta['counts']['shortlisted']}; "
        f"cards {meta['counts']['cards_generated']}.", "",
    ]
    for index, record in enumerate(cards, 1):
        candidate, card = record["candidate"], record["card"]
        missing_inputs = "; ".join(_humanise(str(k)) for k in card.get("missing_reproduction_inputs") or [])
        if not missing_inputs and card.get("reproduction_assessment") is not None:
            missing_inputs = "none reported (AI assessment, unverified)"
        lines += [
            f"## {index}. {candidate['title']}", "",
            f"**Action:** {card['worth_reading']} · **Projects:** "
            f"{', '.join(record['triage']['matched_projects']) or 'none'}", "",
            card["problem"], "",
            f"**Core idea:** {card['core_idea'] or 'not established'}", "",
            f"**Project use:** {_project_use(card['project_use_notes'])}", "",
            f"**Artifacts:** {', '.join(card['code_urls'] + card['dataset_urls'] + card['model_urls']) or 'none verified'}", "",
        ]
        for label, value in (
            ("Reproducibility tier", card.get("reproducibility_tier")),
            ("Reproducibility rationale", card.get("reproducibility_rationale")),
            ("Missing reproduction inputs", missing_inputs),
            ("Evidence gaps", "; ".join(card.get("evidence_gaps") or [])),
        ):
            lines += [f"**{label}:** {value or 'not recorded (not assessed)'}", ""]
    rejected = payload.get("rejected_near_threshold") or []
    if rejected:
        lines += ["## Near-threshold rejects", ""]
        lines += [f"- {row['candidate']['title']} — {row['triage']['rationale']}" for row in rejected]
        lines.append("")
    missing = payload.get("manual_full_text_required") or []
    if missing:
        lines += ["## Needs full text", ""] + [f"- {row['title']}" for row in missing] + [""]
    failures = payload.get("failed") or []
    if failures:
        lines += ["## Failed assessments (not scientific rejections)", ""]
        # Name the paper by title (failed candidates carry theirs in source_history); drop the exception class.
        history = (payload.get("metadata") or {}).get("source_history") or {}
        unscreened = [row for row in failures if row["error"].endswith(NOT_CACHED)]
        for row in failures:
            if row not in unscreened:
                reason = row["error"].partition(": ")[2] or row["error"]
                title = (history.get(row["source_id"]) or {}).get("candidate", {}).get("title") or row["source_id"]
                lines.append(f"- {title}: {PLAIN_REASONS.get(reason, reason)}")
        if unscreened:
            lines.append(f"- {len(unscreened)} papers were not screened yet (cached-only mode skips papers "
                         "without a stored triage; run without --cached-only to screen them).")
        lines.append("")
    unqualified = payload.get("unqualified_reviews") or []
    if unqualified:
        lines += ["## Review qualification pending", ""]
        lines += [f"- {row['candidate']['title']} — {row['reason']} (not evidence of unavailable full text)"
                  for row in unqualified]
        lines.append("")
    return "\n".join(lines)


def persist(
    payload: dict[str, Any], output_dir: Path, slug: str, *,
    render_markdown: Callable[[dict[str, Any]], str] | None = None,
    inputs: list[dict[str, Any]] | None = None,
) -> tuple[Path, Path]:
    if output_dir.is_symlink():
        raise ValueError("report output directory cannot be a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{slug}-", dir=output_dir))
    published = output_dir / slug
    try:
        staged_json = staging / f"{slug}.json"
        staged_markdown = staging / f"{slug}.md"
        write_json_atomic(staged_json, payload)
        renderer = markdown if render_markdown is None else render_markdown
        staged_markdown.write_text(renderer(payload), encoding="utf-8")
        if inputs is not None:
            with (staging / "inputs.jsonl").open("w", encoding="utf-8") as source:
                for post in inputs:
                    source.write(json.dumps(post, ensure_ascii=False, allow_nan=False) + "\n")
        staging.replace(published)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return published / staged_json.name, published / staged_markdown.name


__all__ = ["markdown", "persist"]
