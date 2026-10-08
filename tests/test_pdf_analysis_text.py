import json
import subprocess
import sys
from types import SimpleNamespace

import fitz

from zotero_summarizer.integrations.pdf import OnPremPdfExtractor


def test_analysis_requests_plain_text_and_preserves_page_join(tmp_path):
    path = tmp_path / "source.pdf"
    calls = []

    def loader(file_path, **kwargs):
        calls.append((file_path, kwargs))
        return [
            SimpleNamespace(page_content="Title\nMethods"),
            SimpleNamespace(page_content=""),
            SimpleNamespace(page_content="https://example.org/data"),
        ]

    text = OnPremPdfExtractor(loader).extract_text(path)

    assert calls == [(str(path), {"pdf_markdown": False})]
    assert text == "Title\nMethods\n\nhttps://example.org/data"


def test_onprem_plain_backend_preserves_signed_super_and_subscripts(tmp_path):
    path = tmp_path / "synthetic.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Synthetic uncertainty source: Methods and results", fontsize=12)
        page.insert_text((72, 110), "1.23", fontsize=12)
        page.insert_text((100, 105), "+0.45", fontsize=8)
        page.insert_text((100, 117), "-0.67", fontsize=8)
        page.insert_text((72, 150), "https://example.org/data", fontsize=12)
        document.save(path)

    # conftest installs OnPrem stubs; a fresh interpreter exercises the real loader.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; from onprem.ingest.base import load_single_document; "
            "from zotero_summarizer.integrations.pdf import OnPremPdfExtractor; "
            "print(json.dumps(OnPremPdfExtractor(load_single_document).extract_text(sys.argv[1])))",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    text = json.loads(result.stdout.strip().splitlines()[-1])

    assert "1.23 +0.45\n-0.67" in text
    assert "Synthetic uncertainty source: Methods and results" in text
    assert "https://example.org/data" in text
