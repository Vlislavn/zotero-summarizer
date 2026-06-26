"""Faithfulness mini-benchmark for the deep-review / paper-Q&A pipeline.

See README.md in this package for the build → run → judge → report flow.
"""
from zotero_summarizer.services.faithbench._build_qa import build_items
from zotero_summarizer.services.faithbench._corpus import PaperChunkIndex, select_papers
from zotero_summarizer.services.faithbench._judge import judge_run
from zotero_summarizer.services.faithbench._report import build_report
from zotero_summarizer.services.faithbench._runner import (
    ANSWER_PROMPT,
    RunPaths,
    answer_with_retry,
    run_benchmark,
)

__all__ = [
    "ANSWER_PROMPT",
    "PaperChunkIndex",
    "RunPaths",
    "answer_with_retry",
    "build_items",
    "build_report",
    "judge_run",
    "run_benchmark",
    "select_papers",
]
