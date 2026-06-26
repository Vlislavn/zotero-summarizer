"""Phase 1.8: pdf_fetch enforces %PDF magic and max_bytes."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from zotero_summarizer.integrations.pdf_fetch import fetch_pdf


class _FakeStream:
    """Mimics httpx.Response in a context-manager stream."""

    def __init__(self, status: int, chunks: list[bytes]):
        self.status_code = status
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, chunk_size: int = 64_000):
        for c in self._chunks:
            yield c


def _client_with_stream(stream: _FakeStream) -> MagicMock:
    client = MagicMock(spec=httpx.Client)
    client.stream.return_value = stream
    return client


def test_rejects_response_without_pdf_magic(tmp_path):
    client = _client_with_stream(_FakeStream(200, [b"<html>NOT A PDF</html>"]))
    result = fetch_pdf(
        "https://example.com/x.pdf",
        cache_dir=tmp_path,
        http_client=client,
    )
    assert result is None
    assert not list(tmp_path.iterdir()), "should not have written a non-PDF to cache"


def test_accepts_pdf_with_correct_magic(tmp_path):
    pdf_bytes = b"%PDF-1.7\n%binary stuff\nContent..."
    client = _client_with_stream(_FakeStream(200, [pdf_bytes]))
    result = fetch_pdf(
        "https://example.com/x.pdf",
        cache_dir=tmp_path,
        http_client=client,
    )
    assert result is not None
    assert result.exists()
    assert result.read_bytes() == pdf_bytes


def test_rejects_oversize_response(tmp_path):
    big_chunks = [b"%PDF-1.7\n", b"x" * 200, b"x" * 200, b"x" * 200]
    client = _client_with_stream(_FakeStream(200, big_chunks))
    result = fetch_pdf(
        "https://example.com/x.pdf",
        cache_dir=tmp_path,
        http_client=client,
        max_bytes=300,
    )
    assert result is None


def test_http_4xx_returns_none(tmp_path):
    client = _client_with_stream(_FakeStream(404, []))
    assert (
        fetch_pdf(
            "https://example.com/missing.pdf",
            cache_dir=tmp_path,
            http_client=client,
        )
        is None
    )


def test_cache_hit_short_circuits(tmp_path):
    pdf_bytes = b"%PDF-1.7\nx"
    client = _client_with_stream(_FakeStream(200, [pdf_bytes]))
    first = fetch_pdf(
        "https://example.com/x.pdf",
        cache_dir=tmp_path,
        http_client=client,
    )
    assert first is not None
    # Second call should hit disk cache; passing a None client would crash if used.
    second = fetch_pdf(
        "https://example.com/x.pdf",
        cache_dir=tmp_path,
        http_client=MagicMock(spec=httpx.Client),
    )
    assert second is not None
    assert second == first
