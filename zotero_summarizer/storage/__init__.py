"""Storage package: SQLite persistence for triage, feeds, corpus, and verdicts.

Import the submodules directly (e.g. ``from zotero_summarizer.storage import
repositories as triage_db``). Per-context DB binding is done with
``repositories.with_db_path`` — there is no facade object to construct.
"""
from __future__ import annotations
