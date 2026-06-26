"""``goldenset migrate-verdicts-to-zotero`` — one-time feedback transfer.

Moves every in-app verdict (the ``label_verdicts`` SQLite table) into Zotero as a
``label:<priority>`` tag, so the user's explicit labels live *inside* Zotero (the
source of truth). After this runs, the read path picks the labels back up via
``goldenset.export`` (which reconciles ``label_verdicts`` from the tags).

Library items only: ``feed:``/``note:`` verdicts have no Zotero item to tag and are
reported as skipped. Idempotent — an item already carrying the right ``label:*``
tag yields no change. ``--dry-run`` prints the plan and writes nothing.
"""

from __future__ import annotations

import argparse
import json

from zotero_summarizer.settings import Settings


def _goldenset_migrate_verdicts(args: argparse.Namespace) -> int:
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.integrations.zotero_write import ZoteroWriter
    from zotero_summarizer.services.library.review_detail import (
        SOURCE_FEED,
        SOURCE_NOTE,
        classify_item_key,
    )
    from zotero_summarizer.services.zotero.pending import build_label_tag_change
    from zotero_summarizer.storage import repositories

    settings = Settings.load(project_root=args.project_root)
    reader = ZoteroReader(settings.zotero_data_dir)
    verdicts = repositories.list_label_verdicts(settings.triage_db_path, limit=5000)

    planned: list[dict] = []
    skipped_non_library = 0
    skipped_missing = 0
    already_in_sync = 0
    for verdict in verdicts:
        item_key = verdict["item_key"]
        if classify_item_key(item_key) in (SOURCE_FEED, SOURCE_NOTE):
            skipped_non_library += 1
            continue
        detail = reader.get_item_detail(item_key)
        if detail is None:
            skipped_missing += 1
            continue
        current_tags = [
            str(t or "").strip() for t in (detail.get("tags") or []) if str(t or "").strip()
        ]
        payload = build_label_tag_change(current_tags, verdict["user_priority"])
        if not payload["add_tags"] and not payload["remove_tags"]:
            already_in_sync += 1
            continue
        planned.append({
            "item_key": item_key,
            "user_priority": verdict["user_priority"],
            "add_tags": payload["add_tags"],
            "remove_tags": payload["remove_tags"],
        })

    summary: dict = {
        "verdicts_total": len(verdicts),
        "to_write": len(planned),
        "already_in_sync": already_in_sync,
        "skipped_non_library": skipped_non_library,
        "skipped_missing_in_zotero": skipped_missing,
        "dry_run": bool(args.dry_run),
        "planned": planned,
    }

    if args.dry_run or not planned:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    writer = ZoteroWriter(settings.zotero_data_dir)
    if writer.is_connector_running() and not args.force:
        summary["error"] = "zotero_running"
        summary["message"] = "Zotero appears to be running; close it or pass --force."
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1

    changes = [
        {
            "id": 0,
            "item_key": p["item_key"],
            "change_type": "tag_changes",
            "payload_json": {"add_tags": p["add_tags"], "remove_tags": p["remove_tags"]},
        }
        for p in planned
    ]
    # One backup for the whole batch (create_backup=True), then apply all.
    result = writer.apply_changes(changes, True)
    failed = list(result.get("failed", []))
    summary["applied"] = len(changes) - len(failed)
    summary["failed"] = failed
    summary["backup_path"] = result.get("backup_path")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if not failed else 1


def register_goldenset_migrate(gs_sub) -> None:
    parser = gs_sub.add_parser(
        "migrate-verdicts-to-zotero",
        help=(
            "One-time: transfer every in-app verdict (label_verdicts) into Zotero "
            "as a label:<priority> tag so explicit labels live inside Zotero. "
            "Idempotent; library items only. --dry-run prints the plan first."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; write nothing.")
    parser.add_argument(
        "--force", action="store_true", help="Apply even if Zotero appears to be running.",
    )
    parser.add_argument("--project-root", default=None)
    parser.set_defaults(func=_goldenset_migrate_verdicts)
