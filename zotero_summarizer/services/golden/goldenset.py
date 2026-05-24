"""Golden-set export from Zotero engagement signals (emoji tags, notes,
annotations, trash). Labels follow :mod:`services.emoji_signals` additive
scoring with 180-day decay; see ``docs/feeds.md`` for the scoring table.

``our_*`` columns stay blank — downstream tooling fills them by running the
current algorithm against the export and comparing to the gold labels.

Rows whose ``item_key`` is namespaced (``feed:NNN``, ``note:KEY:ID``) come
from the review UI / analyse-notes flow and are NOT re-derivable from
Zotero — :func:`_write_csv` preserves them across re-exports.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zotero_summarizer.services import emoji_signals
from zotero_summarizer.services._common import atomic_write, connect_sqlite_ro


LOGGER = logging.getLogger(__name__)


@dataclass
class GoldenSample:
    # Identity
    item_key: str
    title: str
    authors: str
    year: str
    venue: str                       # Zotero `publicationTitle` (journal / conference)
    doi: str
    url: str
    abstract: str
    # Raw signals (the inputs the labeller can audit)
    matched_emojis: str              # space-joined list of recognised emojis present
    gold_signal_tier: str            # e.g. strong_positive, critical_engagement, meta
    note_count: int
    annotation_count: int
    collection_count: int
    collections: str
    in_trash: bool
    days_since_added: int
    # Derived labels
    gold_priority_inferred: str
    gold_signal_strength: str
    gold_inferred_relevance: float
    # User-editable
    gold_priority_final: str
    gold_notes: str
    # Placeholders for downstream scoring
    our_composite_score: str = ""
    our_prestige_score: str = ""
    our_priority: str = ""
    our_corpus_affinity: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_golden_dataset(
    zotero_data_dir: Path,
    output_csv: Path,
    output_jsonl: Path,
    *,
    abstract_chars: int = 1000,
    triage_db_path: Path | None = None,
) -> dict[str, Any]:
    """Export the golden-set CSV + JSONL. Returns counts for the CLI to print.

    When ``triage_db_path`` is given, rows for user-verdicted items are preserved
    across the re-export, so a verdict on a materialized library item (e.g. a
    paper added from Today then marked ``dont_read``) isn't dropped when
    "Refresh labels" regenerates the engagement-derived rows.
    """
    db_path = Path(zotero_data_dir) / "zotero.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"zotero.sqlite not found at {db_path}")
    samples = _pull_samples(db_path, abstract_chars=abstract_chars)

    preserve_keys: frozenset[str] = frozenset()
    if triage_db_path is not None:
        from zotero_summarizer.storage import repositories

        # High cap (effectively uncapped): a low limit would silently drop
        # manual verdicts from preserve_keys, losing them on the next re-export.
        preserve_keys = frozenset(
            str(r["item_key"])
            for r in repositories.list_label_verdicts(triage_db_path, limit=1_000_000)
            if r.get("item_key")
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(samples, output_csv, preserve_keys=preserve_keys)
    _write_jsonl(samples, output_jsonl)

    return {
        "total": len(samples),
        "by_class": _class_distribution(samples),
        "by_strength": _strength_distribution(samples),
        "csv_path": str(output_csv),
        "jsonl_path": str(output_jsonl),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_emoji_like_clause() -> str:
    """OR-joined `tags.name LIKE '%EMOJI%'` for every emoji in the taxonomy."""
    return " OR ".join(
        f"t.name LIKE '%{emoji}%'" for emoji in emoji_signals.ALL_EMOJIS
    )


def _count_user_notes(previews: list[str]) -> int:
    """Count notes that are plausibly written by the user (not an LLM).

    Reuses the heuristics from :mod:`services.note_analyzer` so we don't
    double-implement the discriminator. ``previews`` is a list of up to
    600-char snippets pulled by ``user_note_previews`` (SQL already dropped
    rows containing our own ``zs:note_type=`` provenance marker).
    """
    from zotero_summarizer.services.zotero import note_analyzer

    count = 0
    for raw in previews:
        body = note_analyzer._strip_html((raw or "").strip())
        if not body:
            continue
        if note_analyzer._PDF_ANNOT_RE.match(body):
            continue
        if note_analyzer._EXTERNAL_LLM_RE.search(body[:500]):
            continue
        if len(note_analyzer._QUOTE_HEAVY_RE.findall(body)) >= 2:
            continue
        count += 1
    return count


_ENGAGED_ITEMS_SQL = f"""
WITH engaged AS (
    SELECT DISTINCT i.itemID
    FROM items i
    WHERE i.libraryID = 1
      AND (
        EXISTS (SELECT 1 FROM itemTags it JOIN tags t ON t.tagID = it.tagID
                WHERE it.itemID = i.itemID
                  AND ({_build_emoji_like_clause()}))
        OR EXISTS (SELECT 1 FROM itemNotes n WHERE n.parentItemID = i.itemID)
        OR EXISTS (SELECT 1 FROM itemAnnotations a
                   JOIN itemAttachments att ON att.itemID = a.parentItemID
                   WHERE att.parentItemID = i.itemID)
        OR EXISTS (SELECT 1 FROM deletedItems di WHERE di.itemID = i.itemID)
      )
)
SELECT
    i.itemID,
    i.key AS item_key,
    i.dateAdded,
    (
      SELECT v.value FROM itemData id
      JOIN fields f ON f.fieldID = id.fieldID
      JOIN itemDataValues v ON v.valueID = id.valueID
      WHERE id.itemID = i.itemID AND f.fieldName = 'title' LIMIT 1
    ) AS title,
    (
      SELECT v.value FROM itemData id
      JOIN fields f ON f.fieldID = id.fieldID
      JOIN itemDataValues v ON v.valueID = id.valueID
      WHERE id.itemID = i.itemID AND f.fieldName = 'abstractNote' LIMIT 1
    ) AS abstract,
    (
      SELECT v.value FROM itemData id
      JOIN fields f ON f.fieldID = id.fieldID
      JOIN itemDataValues v ON v.valueID = id.valueID
      WHERE id.itemID = i.itemID AND f.fieldName = 'date' LIMIT 1
    ) AS publication_date,
    (
      SELECT v.value FROM itemData id
      JOIN fields f ON f.fieldID = id.fieldID
      JOIN itemDataValues v ON v.valueID = id.valueID
      WHERE id.itemID = i.itemID AND f.fieldName = 'publicationTitle' LIMIT 1
    ) AS publication_title,
    (
      SELECT v.value FROM itemData id
      JOIN fields f ON f.fieldID = id.fieldID
      JOIN itemDataValues v ON v.valueID = id.valueID
      WHERE id.itemID = i.itemID AND f.fieldName = 'DOI' LIMIT 1
    ) AS doi,
    (
      SELECT v.value FROM itemData id
      JOIN fields f ON f.fieldID = id.fieldID
      JOIN itemDataValues v ON v.valueID = id.valueID
      WHERE id.itemID = i.itemID AND f.fieldName = 'url' LIMIT 1
    ) AS url,
    COALESCE((
      SELECT group_concat(
          CASE WHEN c.fieldMode = 1 THEN COALESCE(c.lastName, '')
               ELSE trim(COALESCE(c.firstName, '') || ' ' || COALESCE(c.lastName, ''))
          END, '; ')
      FROM itemCreators ic JOIN creators c ON c.creatorID = ic.creatorID
      WHERE ic.itemID = i.itemID
    ), '') AS authors,
    COALESCE((
      SELECT group_concat(t.name, '|||')
      FROM itemTags itg JOIN tags t ON t.tagID = itg.tagID
      WHERE itg.itemID = i.itemID
    ), '') AS tag_blob,
    COALESCE((
      SELECT group_concat(c.collectionName, '|||')
      FROM collectionItems ci JOIN collections c ON c.collectionID = ci.collectionID
      WHERE ci.itemID = i.itemID
    ), '') AS collection_blob,
    (
      SELECT COUNT(*) FROM itemNotes n
      WHERE n.parentItemID = i.itemID
        AND n.note NOT LIKE '%zs:note_type=%'
    ) AS raw_user_note_count,
    (
      SELECT group_concat(substr(n.note, 1, 600), '|||')
      FROM itemNotes n
      WHERE n.parentItemID = i.itemID
        AND n.note NOT LIKE '%zs:note_type=%'
    ) AS user_note_previews,
    (
      SELECT COUNT(*) FROM itemAnnotations a
      JOIN itemAttachments att ON att.itemID = a.parentItemID
      WHERE att.parentItemID = i.itemID
    ) AS annotation_count,
    (SELECT 1 FROM deletedItems di WHERE di.itemID = i.itemID) AS in_trash
FROM items i
WHERE i.itemID IN engaged
ORDER BY i.dateAdded DESC
"""


def _pull_samples(db_path: Path, *, abstract_chars: int) -> list[GoldenSample]:
    conn = connect_sqlite_ro(db_path)
    try:
        rows = conn.execute(_ENGAGED_ITEMS_SQL).fetchall()
    finally:
        conn.close()

    samples: list[GoldenSample] = []
    now = datetime.now(timezone.utc)
    for row in rows:
        tags = [t for t in (row["tag_blob"] or "").split("|||") if t]
        collections = [c for c in (row["collection_blob"] or "").split("|||") if c]
        in_trash = bool(row["in_trash"])
        # Phase 1.15: note_count must exclude LLM-written notes. SQL already
        # dropped our own zs:note_type=triage rows; this further drops
        # external-LLM-pasted notes (ChatGPT/Claude/Gemini/Perplexity) using
        # the heuristics already developed for `services.note_analyzer`.
        note_previews = (row["user_note_previews"] or "").split("|||")
        note_count = _count_user_notes(note_previews)
        annotation_count = int(row["annotation_count"] or 0)

        signals = emoji_signals.detect_signals(tags)
        days_since = _days_since(row["dateAdded"], now)
        priority, strength, relevance, tier = _infer_label(
            tags=tags,
            in_trash=in_trash,
            note_count=note_count,
            annotation_count=annotation_count,
            days_since_added=days_since,
        )
        matched = " ".join(sorted({s.emoji for s in signals}))
        abstract = (row["abstract"] or "").strip()
        if abstract_chars and len(abstract) > abstract_chars:
            abstract = abstract[:abstract_chars].rstrip() + "…"

        samples.append(
            GoldenSample(
                item_key=str(row["item_key"]),
                title=(row["title"] or "").strip(),
                authors=row["authors"] or "",
                year=_extract_year(row["publication_date"]),
                venue=(row["publication_title"] or "").strip(),
                doi=(row["doi"] or "").strip(),
                url=(row["url"] or "").strip(),
                abstract=abstract,
                matched_emojis=matched,
                gold_signal_tier=tier,
                note_count=note_count,
                annotation_count=annotation_count,
                collection_count=len(collections),
                collections="; ".join(collections),
                in_trash=in_trash,
                days_since_added=days_since,
                gold_priority_inferred=priority,
                gold_signal_strength=strength,
                gold_inferred_relevance=relevance,
                gold_priority_final=priority,  # editable copy
                gold_notes="",
            )
        )
    return samples


def _infer_label(
    *,
    tags: list[str],
    in_trash: bool,
    note_count: int,
    annotation_count: int,
    days_since_added: int = 0,
) -> tuple[str, str, float, str]:
    """Additive scoring → (priority, strength, inferred_relevance, tier_audit).

    Phase 1.14 design (user-confirmed 2026-05-14): we no longer pick a
    single winning tier. We accumulate score contributions:

      * baseline ``3.0`` (neutral)
      * + sum of emoji ``score_delta`` for every recognised emoji tag
      * + capped additive from ``annotation_count``
      * + capped additive from ``note_count``

    Phase 1.15 (user-confirmed 2026-05-14): the engagement contribution
    (everything except the neutral baseline and the hard-veto short-
    circuits) is multiplied by an exponential decay factor based on
    ``days_since_added`` — 180-day half-life. Recent labels carry full
    weight; year-old labels weigh ~1/4.

    Hard short-circuits (irrevocable, applied BEFORE scoring):
      * ``in_trash`` → dont_read (1.0).
      * Any hard-veto emoji (🥱 / 👎 / ❌) → dont_read (1.0).

    The aggregate score is binned into the 4-class reading priority; the
    fourth return value is the ``|``-joined list of tiers that contributed
    (for the ``gold_signal_tier`` audit column).
    """
    if in_trash:
        return "dont_read", "high", 1.0, "trash"

    if emoji_signals.has_hard_veto(tags):
        return "dont_read", "high", 1.0, "hard_veto"

    signals = emoji_signals.detect_signals(tags)
    engagement_sum = (
        emoji_signals.score_signals(signals)
        + emoji_signals.score_annotations(annotation_count)
        + emoji_signals.score_notes(note_count)
    )
    weight = emoji_signals.decay_weight(days_since_added)
    score = emoji_signals.NEUTRAL_SCORE + weight * engagement_sum
    score = max(1.0, min(5.0, score))   # clamp to the 1..5 relevance range

    priority = emoji_signals.priority_for_score(score)
    strength = emoji_signals.strength_for_score(score, num_signals=len(signals))
    tier_audit = _format_tier_audit(signals, annotation_count, note_count)
    return priority, strength, round(float(score), 2), tier_audit


def _format_tier_audit(
    signals: set,
    annotation_count: int,
    note_count: int,
) -> str:
    """Audit-only label persisted in ``gold_signal_tier``. Stable shape so
    downstream analytics can ``WHERE gold_signal_tier LIKE '%boring%'`` etc."""
    parts: list[str] = sorted({s.tier for s in signals})
    if annotation_count > 0:
        parts.append(f"ann={annotation_count}")
    if note_count > 0:
        parts.append(f"notes={note_count}")
    return "|".join(parts) if parts else "meta"


def _extract_year(date_str: Any) -> str:
    if not date_str:
        return ""
    s = str(date_str).strip()
    if len(s) >= 4 and s[:4].isdigit():
        return s[:4]
    return ""


def _days_since(date_str: Any, now: datetime) -> int:
    if not date_str:
        return -1
    s = str(date_str).strip()
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T")[:19])
        return max(0, (now - dt.replace(tzinfo=timezone.utc)).days)
    except ValueError:
        return -1


def _write_csv(
    samples: list[GoldenSample], path: Path, *, preserve_keys: frozenset[str] = frozenset(),
) -> None:
    """Write Zotero-derived ``samples`` and preserve existing rows that aren't
    re-derivable from Zotero: namespaced keys (``feed:*`` / ``note:*``) and any
    key in ``preserve_keys`` (user-verdicted items — e.g. a materialized library
    paper marked ``dont_read`` that the engagement-only export wouldn't emit).
    """
    if not samples:
        path.write_text("")
        return
    fieldnames = list(asdict(samples[0]).keys())
    sample_keys = {s.item_key for s in samples}

    preserved: list[dict[str, str]] = []
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "item_key" not in reader.fieldnames:
                raise ValueError(
                    f"existing golden CSV {path} has no item_key column — "
                    f"refusing to overwrite it"
                )
            for row in reader:
                key = row["item_key"]
                if key not in sample_keys and (":" in key or key in preserve_keys):
                    preserved.append({c: row.get(c, "") for c in fieldnames})

    def _write(target: Path) -> None:
        with target.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for s in samples:
                writer.writerow(asdict(s))
            for r in preserved:
                writer.writerow(r)

    # tmp + os.replace: a crash mid-write must never truncate the golden CSV
    # (months of labels live here and the preserved rows are already in memory).
    atomic_write(path, _write)


def _write_jsonl(samples: list[GoldenSample], path: Path) -> None:
    def _write(target: Path) -> None:
        with target.open("w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(asdict(s), ensure_ascii=False) + "\n")

    atomic_write(path, _write)


def _class_distribution(samples: list[GoldenSample]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in samples:
        out[s.gold_priority_inferred] = out.get(s.gold_priority_inferred, 0) + 1
    return out


def _strength_distribution(samples: list[GoldenSample]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in samples:
        out[s.gold_signal_strength] = out.get(s.gold_signal_strength, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Priority -> relevance mapping (used by services.review when appending
# user-approved review rows to the golden CSV).
# ---------------------------------------------------------------------------


# Single source of truth (domain). Re-exported under the legacy private name so
# existing importers (services.library.review / review_summary) keep working.
# Previously should_read was 4.5 here (the must_read boundary) — a silent bug
# that trained review-appended should_read rows toward must_read.
from zotero_summarizer.domain import PRIORITY_TO_RELEVANCE as _PRIORITY_TO_RELEVANCE  # noqa: E402
