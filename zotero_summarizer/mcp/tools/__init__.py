from __future__ import annotations

_REGISTERED = False


def register_tools() -> None:
	"""Import tool modules once so FastMCP decorators register handlers."""
	global _REGISTERED
	if _REGISTERED:
		return

	from zotero_summarizer.mcp.tools import mutations, pending, search, status, triage

	_ = (search, triage, mutations, pending, status)
	_REGISTERED = True
