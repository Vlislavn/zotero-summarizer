from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from zotero_summarizer.settings import Settings


@dataclass
class AppContext:
    settings: Settings
    state: SimpleNamespace = field(default_factory=SimpleNamespace)


_context: AppContext | None = None


def set_context(context: AppContext) -> None:
    global _context
    _context = context


def get_context() -> AppContext:
    if _context is None:
        set_context(AppContext(settings=Settings.load()))
    assert _context is not None
    return _context


class _AppProxy:
    @property
    def state(self) -> SimpleNamespace:
        return get_context().state


app_proxy = _AppProxy()
