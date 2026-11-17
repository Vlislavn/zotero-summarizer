"""``review-feedback list|check``: read the saved review-feedback packages and record what was concluded.

Local files only (``Settings.review_feedback_dir``): no model, no network, no database. A package whose
snapshot stayed ``pending`` for over 10 minutes is marked ``interrupted`` here; the snapshot itself is
retried by the app on the owner's next save.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path



def _services():
    """Imported on use: building the parser must stay light."""
    from zotero_summarizer.runtime import get_context
    from zotero_summarizer.services.library import review_feedback_check as check
    from zotero_summarizer.services.library import review_feedback_snapshot as snapshot
    from zotero_summarizer.services.library.review_feedback_bundle import load_json

    return get_context().settings.review_feedback_dir, check, snapshot, load_json


def _packages(root: Path) -> list[Path]:
    return sorted(feedback.parent for feedback in root.glob("*/*/feedback.json"))


def _label(part: dict) -> str:
    return "withdrawn" if part.get("withdrawn") else ",".join(part.get("chips") or []) or "note"


def _list(args: argparse.Namespace) -> int:
    root, check, snapshot, load_json = _services()
    rows = []
    for pkg in _packages(root):
        snapshot.mark_interrupted(pkg)
        data = load_json(pkg / "feedback.json") or {}
        parts = "; ".join(f"{field}[{_label(part)}]" for field, part in (data.get("parts") or {}).items()) or "-"
        checks = ",".join(f"{field}={entry['status']}" for field, entry in check.read_checks(pkg).items()) or "-"
        title = ((data.get("article") or {}).get("title") or "")[:40]
        rows.append((str(data.get("item_key", "?")), title, parts, snapshot.read(pkg).get("status") or "none", checks,
                     str(pkg.relative_to(root.parent))))
    if not rows:
        print("No review feedback saved yet.")
        return 0
    print("\t".join(("key", "title", "parts[reasons]", "snapshot", "checked", "path")))
    for row in rows:
        print("\t".join(row))
    return 0


def _check(args: argparse.Namespace) -> int:
    root, check, snapshot, load_json = _services()
    found = [pkg for pkg in _packages(root) if (load_json(pkg / "feedback.json") or {}).get("item_key") == args.item_key]
    if not found:
        print(f"error: no review feedback saved for {args.item_key}", file=sys.stderr)
        return 1
    pkg = found[-1] if not args.version else next((p for p in found if p.name == args.version), None)
    if pkg is None:
        print(f"error: no version {args.version!r}; have {', '.join(p.name for p in found)}", file=sys.stderr)
        return 1
    snapshot.mark_interrupted(pkg)
    if args.set:
        field, _, status = args.set.partition("=")
        try:
            check.write_check(pkg, field, status, args.note or "")
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    report = check.inspect(pkg, [args.part] if args.part else None)
    print(f"{report['item_key']}  {(report['article'] or {}).get('title', '')}\n{pkg}  snapshot: {snapshot.read(pkg).get('status', 'none')}"
          f"  source text: {'yes' if report['source_text'] else 'no'}")
    for field, part in report["parts"].items():
        print(f"\n[{field}] {_label(part)}" + (f"  note: {part['note']}" if part["note"] else ""))
        print(f"  shown: {part['shown'] or '-'}")
        for hit in part["phrases"]:
            where = f"@{hit['offset']} ({int(hit['share'] * 100)}%)" if "offset" in hit else ""
            print(f"  {hit['match']:>10} {where}  {hit['phrase'][:90]}")
        if part["checked"]:
            print(f"  checked: {part['checked']['status']} — {part['checked']['note']}")
    return 0


def register_review_feedback(subparsers) -> None:
    group = subparsers.add_parser("review-feedback", help="Inspect saved review feedback packages (local files, no model)")
    sub = group.add_subparsers(dest="review_feedback_command", required=True)
    listing = sub.add_parser("list", help="One line per saved package: paper, parts and reasons, snapshot, checks")
    listing.add_argument("--project-root", default=None)
    listing.set_defaults(func=_list)
    verify = sub.add_parser("check", help="Show each part's shown text and look for it in the paper text; optionally record a verdict")
    verify.add_argument("item_key")
    verify.add_argument("--version", default=None, help="package folder name (default: the latest)")
    verify.add_argument("--part", default=None, help="only this part (e.g. red_flags, whole)")
    verify.add_argument("--set", metavar="PART=STATUS", help="record the verdict: STATUS is confirmed, rejected or unclear")
    verify.add_argument("--note", default="", help="note for --set (at most 500 characters)")
    verify.add_argument("--project-root", default=None)
    verify.set_defaults(func=_check)
