from __future__ import annotations

from fastapi.responses import FileResponse

from zotero_summarizer.services._common import html_file_response, web_file


async def index_page() -> FileResponse:
    return html_file_response(web_file("ui.html"))


async def dashboard_page() -> FileResponse:
    return html_file_response(web_file("dashboard.html"))
