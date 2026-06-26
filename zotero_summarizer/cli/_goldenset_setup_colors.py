"""``goldenset setup-tag-colors`` — the one-time Zotero setup for native
keypress labeling.

Zotero lets you assign a COLOR and a NUMBER KEY (1-9) to a tag; pressing the
number toggles that tag on the selected item(s). Assigning keys 1-4 to the four
``label:<priority>`` tags makes labeling a single native keypress — the same
ground truth the app reads back (Jakob's Law: your existing Zotero muscle memory,
no new app paradigm).

This command is **non-destructive**: it prints the exact plan + the ~2-minute
manual steps and writes nothing. Tag colors live in Zotero 7's ``syncedSettings``
of your *synced* library; writing an unverified blob there is not worth the risk
for a cosmetic + keybinding convenience. (A verified auto-writer is a safe
follow-up once a sample color is set in Zotero and its exact JSON can be read.)
"""

from __future__ import annotations

import argparse
import json

from zotero_summarizer.domain import label_tag_for_priority


# Number key 1-4 in priority order (must_read first), each with a distinct,
# severity-ordered color. The position (1-4) becomes the Zotero number key.
_PLAN: tuple[tuple[str, str, str], ...] = (
    ("must_read", "#2E7D32", "green  — read now"),
    ("should_read", "#1565C0", "blue   — worth reading"),
    ("could_read", "#F9A825", "amber  — maybe / later"),
    ("dont_read", "#9E9E9E", "grey   — skip"),
)


def _plan_rows() -> list[dict]:
    """The deterministic {key, tag, color, hint} plan, keys 1-4 in priority order."""
    return [
        {"key": position, "tag": label_tag_for_priority(priority), "color": color, "hint": hint}
        for position, (priority, color, hint) in enumerate(_PLAN, start=1)
    ]


def _goldenset_setup_tag_colors(args: argparse.Namespace) -> int:
    rows = _plan_rows()
    if args.json:
        print(json.dumps({"tag_colors": rows}, indent=2, ensure_ascii=False))
        return 0
    print("Native keypress labeling in Zotero — one-time setup (~2 min):\n")
    print("  key  tag                color")
    print("  ---  -----------------  -------------------------")
    for r in rows:
        print(f"   {r['key']}   {r['tag']:<17}  {r['color']}  {r['hint']}")
    print()
    print("In Zotero: open the Tag Selector (bottom-left of the library pane),")
    print('right-click each tag above -> "Assign Color" -> pick the color AND the')
    print("position (1-4). Then select any item(s) and press 1/2/3/4 to toggle the")
    print("matching label:<priority> tag — the same ground truth the app reads back")
    print("on the next `goldenset export`.")
    print()
    print("Non-destructive: this prints the plan only. Tag colors live in your")
    print("*synced* Zotero settings, so you stay in control of the write.")
    return 0


def register_goldenset_setup_tag_colors(gs_sub) -> None:
    parser = gs_sub.add_parser(
        "setup-tag-colors",
        help=(
            "Print the one-time Zotero setup (colors + number keys 1-4 for the four "
            "label:<priority> tags) so labeling is a native keypress. Non-destructive "
            "— prints the plan, writes nothing. --json for machine output."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit the plan as JSON.")
    parser.set_defaults(func=_goldenset_setup_tag_colors)
