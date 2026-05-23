from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Generator

from zotero_summarizer.settings import Settings


def _resolve_feed_ids(raw: str, settings: Settings) -> list[int]:
    """Resolve a comma-separated string of feed tokens to library IDs.

    Each token may be:
    - A numeric string (used directly as ``library_id``)
    - A name substring (case-insensitive match against Zotero feed names)

    Raises ``SystemExit`` with a descriptive message on ambiguity or no match.
    """
    from zotero_summarizer.integrations.zotero_read import ZoteroReader
    from zotero_summarizer.services.triage.feeds import list_feed_groups

    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        return []

    needs_name_lookup = any(not t.lstrip("-").isdigit() for t in tokens)
    feed_groups: list[dict] = []
    if needs_name_lookup:
        try:
            feed_groups = list_feed_groups(ZoteroReader(settings.zotero_data_dir))
        except Exception as exc:
            print(f"ERROR: could not read feed list from Zotero: {exc}", file=sys.stderr)
            raise SystemExit(2)

    ids: list[int] = []
    for token in tokens:
        if token.lstrip("-").isdigit():
            ids.append(int(token))
        else:
            matches = [f for f in feed_groups if token.lower() in f["name"].lower()]
            if not matches:
                available = ", ".join(
                    f'"{f["name"]}" (ID {f["library_id"]})' for f in feed_groups[:8]
                )
                print(
                    f"ERROR: no feed matches {token!r}.\n"
                    f"Run `feeds list` to see all feeds. Some options: {available}",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            if len(matches) > 1:
                options = ", ".join(f'"{m["name"]}" (ID {m["library_id"]})' for m in matches)
                print(
                    f"ERROR: ambiguous feed name {token!r} — {len(matches)} matches: {options}.\n"
                    "Be more specific or use the numeric ID from `feeds list`.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            ids.append(int(matches[0]["library_id"]))
    return ids


@contextlib.contextmanager
def _feeds_lock(project_root: Path) -> Generator[None, None, None]:
    """Exclusive lock that prevents simultaneous `feeds run` / `feeds serve` calls.

    Uses a PID file at ``{project_root}/feeds.lock``.  If the PID in the file
    belongs to an active process the command exits with an error message.
    Stale locks (dead PID) are silently overwritten.

    ``feeds tick`` and ``feeds select-daily`` do NOT use this lock — they are
    explicitly designed to be run alongside a daemon.
    """
    lock_path = project_root / "feeds.lock"
    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text().strip())
            os.kill(existing_pid, 0)  # Signal 0: raises if process does not exist
            print(
                f"ERROR: a feeds process is already running (PID {existing_pid}).\n"
                "Stop it first (Ctrl-C or kill), then retry.\n"
                "Tip: `feeds tick` can run alongside the daemon for a one-shot batch.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        except (ProcessLookupError, PermissionError):
            pass  # Stale lock — overwrite it
        except ValueError:
            pass  # Corrupt lock file — overwrite it

    lock_path.write_text(str(os.getpid()))
    try:
        yield
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _slugify_model(model_name: str) -> str:
    """Make a filesystem-safe / column-safe classifier slug from a model name.

    ``nvidia/nemotron-3-super-120b-a12b:free`` → ``llm_nvidia_nemotron_3_super``.
    """
    import re

    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", model_name).strip("_").lower()
    parts = cleaned.split("_")[:5]
    return "llm_" + "_".join(parts)


def _utc_iso_now() -> str:
    from zotero_summarizer.services._common import now_iso_z
    return now_iso_z()


def _persist_run_log(settings: Settings, entry: dict) -> None:
    """Append the run entry to classifier-runs.jsonl + write a markdown report.

    Mutates ``entry`` in place to include the on-disk paths so the caller's
    JSON output points at the persisted artefacts.
    """
    from zotero_summarizer.services import run_log

    log_path = settings.data_dir / "classifier-runs.jsonl"
    run_log.append_run(log_path, entry)
    reports_dir = settings.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{entry['run_id']}.md"
    report_path.write_text(_format_run_report_md(entry), encoding="utf-8")
    entry["run_log_path"] = str(log_path)
    entry["report_path"] = str(report_path)


def _format_run_report_md(entry: dict) -> str:
    lines = [
        f"# {entry['run_id']}",
        "",
        f"- **classifier**: `{entry['classifier']}`",
        f"- **timestamp**: {entry['timestamp']}",
        f"- **git commit**: `{entry.get('git_commit') or '(no commit)'}`",
        f"- **input CSV**: `{entry['input_csv']}` (sha256={entry.get('input_csv_sha256_prefix', '')})",
        f"- **config**: {entry.get('config', {})}",
        f"- **thresholds**: {entry.get('thresholds', {})}",
        "",
    ]
    for split_name in ("cv", "holdout"):
        block = entry.get(split_name) or {}
        if not block:
            continue
        lines.append(f"## {split_name.upper()}")
        lines.append("")
        lines.append(f"- AUC: **{block.get('auc')}** · n={block.get('n_rows')} · positives={block.get('n_positive')}")
        m = block.get("metrics_vs_gold") or {}
        if m.get("total", 0) > 0:
            b = m.get("binary", {})
            lines.append(
                f"- binary keep: P=**{b.get('precision')}** R=**{b.get('recall')}** "
                f"F1=**{b.get('f1')}** (support={b.get('support')})"
            )
            pc = m.get("per_class", {}).get("must_read", {})
            lines.append(
                f"- must_read: P={pc.get('precision')} R={pc.get('recall')} F1={pc.get('f1')} "
                f"(support={pc.get('support')})"
            )
        lines.append("")
    return "\n".join(lines)
