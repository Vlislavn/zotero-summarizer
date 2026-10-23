"""Faithfulness benchmark exports; lightweight input admission never starts execution."""
from importlib import import_module

_EXPORTS = {
    "ANSWER_PROMPT": "_runner", "PaperChunkIndex": "_corpus", "RunInputs": "_runner",
    "RunOptions": "_options", "RunPaths": "_runner", "answer_with_retry": "_runner",
    "build_items": "_build_qa", "build_report": "_report", "judge_run": "_judge",
    "run_benchmark": "_runner", "select_papers": "_corpus",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{_EXPORTS[name]}"), name)
    globals()[name] = value
    return value
