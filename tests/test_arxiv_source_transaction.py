"""Offline, network and publication boundaries for opt-in TeX acquisition."""
from __future__ import annotations

import io
import socket
import tarfile
from pathlib import Path
from types import SimpleNamespace

import fitz
import httpx
import pytest

from zotero_summarizer.integrations import app_rss
from zotero_summarizer.services.library import _paper_read_tex as tex
from zotero_summarizer.services.library import paper_render


def _archive(*, extra=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        body = b"\\documentclass{article}\\section{Methods}Validated source."
        member = tarfile.TarInfo("nested/main.tex")
        member.size = len(body)
        tar.addfile(member, io.BytesIO(body))
        if extra is not None:
            tar.addfile(extra)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def online(monkeypatch):
    monkeypatch.delenv("ZS_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)


@pytest.mark.parametrize("env,value", [("ZS_OFFLINE", "1"), ("ZS_OFFLINE", "true"), ("HF_HUB_OFFLINE", "1")])
def test_offline_stops_before_downloader_or_filesystem(tmp_path, monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    monkeypatch.setattr(tex, "_download_capped", lambda _: pytest.fail("offline network attempt"))
    source = tmp_path / "new-parent" / "source"
    assert tex.download_arxiv_source("2401.00001", source) is None
    assert not source.parent.exists()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("error", [OSError("disk interrupted"), tarfile.TarError("bad member")])
def test_partial_extract_never_publishes_and_retry_is_clean(tmp_path, monkeypatch, existing, error):
    source = tmp_path / "source"
    if existing:
        source.mkdir()
    monkeypatch.setattr(tex, "_download_capped", lambda _: _archive())
    extract = tex._safe_extract

    def interrupted(tar, target):
        (target / "partial.tex").write_text("must not be discovered")
        raise error

    monkeypatch.setattr(tex, "_safe_extract", interrupted)
    with pytest.raises(type(error), match=str(error)):
        tex.download_arxiv_source("2401.00001", source)
    assert not source.exists() or list(source.iterdir()) == []
    assert set(tmp_path.iterdir()) == ({source} if existing else set())

    monkeypatch.setattr(tex, "_safe_extract", extract)
    assert tex.download_arxiv_source("2401.00001", source) == source
    assert (source / "nested/main.tex").read_bytes().endswith(b"Validated source.")
    assert not (source / "partial.tex").exists()
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("payload", [b"", b"not a tar archive"])
def test_invalid_payload_is_an_error_not_pdf_fallback(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(tex, "_download_capped", lambda _: payload)
    with pytest.raises(tarfile.TarError):
        tex.download_arxiv_source("2401.00001", tmp_path / "source")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["README", "empty.tex"])
def test_archive_without_nonempty_tex_is_rejected(tmp_path, monkeypatch, name):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        archive.addfile(tarfile.TarInfo(name))
    monkeypatch.setattr(tex, "_download_capped", lambda _: buffer.getvalue())
    with pytest.raises(tarfile.TarError, match="TeX"):
        tex.download_arxiv_source("2401.00001", tmp_path / "source")
    assert list(tmp_path.iterdir()) == []


def test_existing_source_is_never_overwritten(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "notes.txt").write_bytes(b"user-owned")
    monkeypatch.setattr(tex, "_download_capped", lambda _: pytest.fail("must reject before download"))
    with pytest.raises(FileExistsError):
        tex.download_arxiv_source("2401.00001", source)
    assert (source / "notes.txt").read_bytes() == b"user-owned"


def test_tarfile_nonfatal_errors_also_prevent_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(tex, "_download_capped", lambda _: _archive())

    def failed_metadata(*_a, **_k):
        raise tarfile.ExtractError("could not set file metadata")

    monkeypatch.setattr(tarfile.TarFile, "utime", failed_metadata)
    with pytest.raises(tarfile.ExtractError, match="file metadata"):
        tex.download_arxiv_source("2401.00001", tmp_path / "source")
    assert list(tmp_path.iterdir()) == []


def test_failed_publication_preserves_source_and_cleans_stage(tmp_path, monkeypatch):
    source = tmp_path / "source"
    monkeypatch.setattr(tex, "_download_capped", lambda _: _archive())
    replace = Path.replace

    def collision(path, target):
        if target == source:
            source.mkdir()
            (source / "keep.txt").write_bytes(b"concurrent data")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", collision)
    with pytest.raises(OSError):
        tex.download_arxiv_source("2401.00001", source)
    assert (source / "keep.txt").read_bytes() == b"concurrent data"
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_archive_links_and_special_files_are_not_published(tmp_path, monkeypatch, kind):
    extra = tarfile.TarInfo("alias")
    extra.type, extra.linkname = kind, "nested/main.tex"
    monkeypatch.setattr(tex, "_download_capped", lambda _: _archive(extra=extra))
    with pytest.raises(tarfile.TarError):
        tex.download_arxiv_source("2401.00001", tmp_path / "source")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["../outside", "/absolute"])
def test_archive_traversal_leaves_no_discoverable_tex(tmp_path, monkeypatch, name):
    monkeypatch.setattr(tex, "_download_capped", lambda _: _archive(extra=tarfile.TarInfo(name)))
    with pytest.raises(tarfile.TarError):
        tex.download_arxiv_source("2401.00001", tmp_path / "source")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("limit", ["_MAX_MEMBERS", "_MAX_EXTRACT_BYTES"])
def test_archive_caps_raise_without_publication(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(tex, limit, 0)
    monkeypatch.setattr(tex, "_download_capped", lambda _: _archive())
    with pytest.raises(tarfile.TarError):
        tex.download_arxiv_source("2401.00001", tmp_path / "source")
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def network(monkeypatch):
    responses, requests, options = [], [], []

    def respond(request):
        requests.append(request)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(app_rss.socket, "getaddrinfo", lambda *_a, **_k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ])
    monkeypatch.setattr(tex.httpx, "Client", lambda **kwargs: options.append(kwargs) or client)
    monkeypatch.setattr(tex.httpx, "stream", lambda method, url, **kwargs: client.stream(
        method, url, follow_redirects=kwargs.get("follow_redirects", False),
    ))
    yield responses, requests, options
    client.close()


def _response(status=200, body=b"source bytes", **headers):
    return httpx.Response(status, stream=httpx.ByteStream(body), headers=headers)


def test_public_redirects_reenter_pinned_boundary(network):
    responses, requests, options = network
    responses.extend([_response(302, location="/src/2401.00001"), _response()])
    assert tex._download_capped("https://arxiv.org/e-print/2401.00001") == b"source bytes"
    assert len(requests) == 2
    assert all(request.url.host == "93.184.216.34" for request in requests)
    assert all(request.headers["host"] == "arxiv.org" for request in requests)
    assert all(request.extensions["sni_hostname"] == "arxiv.org" for request in requests)
    assert options[0]["trust_env"] is False


def test_private_redirect_never_reaches_transport(network):
    responses, requests, _ = network
    responses.extend([_response(302, location="https://127.0.0.1/secret"), _response()])
    with pytest.raises(app_rss.RssUrlRejected):
        tex._download_capped("https://arxiv.org/e-print/2401.00001")
    assert len(requests) == 1


def test_redirect_cannot_downgrade_https(network):
    responses, requests, _ = network
    responses.extend([_response(302, location="http://arxiv.org/source"), _response()])
    with pytest.raises(ValueError, match="HTTPS"):
        tex._download_capped("https://arxiv.org/e-print/2401.00001")
    assert len(requests) == 1


@pytest.mark.parametrize("status", [403, 429, 500])
def test_http_failures_propagate(network, status):
    responses, _, _ = network
    responses.append(_response(status))
    with pytest.raises(httpx.HTTPStatusError):
        tex._download_capped("https://arxiv.org/e-print/2401.00001")


def test_404_is_explicit_absence_without_creating_source(tmp_path, network):
    responses, _, _ = network
    responses.append(_response(404))
    assert tex.download_arxiv_source("2401.00001", tmp_path / "source") is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("error", [httpx.ReadTimeout("network failure"), OSError("OS failure")])
def test_network_failures_remain_visible(tmp_path, network, error):
    responses, _, _ = network
    responses.append(error)
    with pytest.raises(type(error), match=str(error)):
        tex.download_arxiv_source("2401.00001", tmp_path / "source")
    assert list(tmp_path.iterdir()) == []


def test_response_limit_raises_instead_of_falling_back(network, monkeypatch):
    responses, _, _ = network
    monkeypatch.setattr(tex, "_MAX_ARCHIVE_BYTES", 4)
    responses.append(_response(body=b"12345"))
    with pytest.raises(ValueError, match="size"):
        tex._download_capped("https://arxiv.org/e-print/2401.00001")


def test_compressed_http_body_is_rejected_before_decoding(network):
    responses, _, _ = network
    responses.append(_response(body=b"not gzip", **{"content-encoding": "gzip"}))
    with pytest.raises(ValueError, match="encoding"):
        tex._download_capped("https://arxiv.org/e-print/2401.00001")


def test_redirect_loop_is_bounded_and_visible(network):
    responses, requests, _ = network
    responses.extend(_response(302, location="/again") for _ in range(30))
    with pytest.raises((ValueError, httpx.TooManyRedirects)):
        tex._download_capped("https://arxiv.org/e-print/2401.00001")
    assert len(requests) <= 6


def test_size_limit_stops_reading_before_the_remaining_body(network, monkeypatch):
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 65536
            yield b"y" * 65536
            pytest.fail("read beyond the size limit")

    responses, _, _ = network
    responses.append(httpx.Response(200, stream=Body()))
    monkeypatch.setattr(tex, "_MAX_ARCHIVE_BYTES", 65536)
    with pytest.raises(ValueError, match="size"):
        tex._download_capped("https://arxiv.org/e-print/2401.00001")


@pytest.mark.parametrize("dangling", [False, True])
def test_source_symlink_is_rejected_before_download(tmp_path, monkeypatch, dangling):
    target = tmp_path / "other-paper"
    if not dangling:
        target.mkdir()
    source = tmp_path / "source"
    source.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(tex, "_download_capped", lambda _: pytest.fail("must reject before download"))
    with pytest.raises(ValueError, match="symlink"):
        tex.download_arxiv_source("2401.00001", source)
    assert source.is_symlink()
    assert not target.exists() or list(target.iterdir()) == []


@pytest.fixture
def paper(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Source Transaction Paper", fontsize=18)
        page.insert_text((72, 100), "arXiv:2401.00001v2", fontsize=9)
        page.insert_text((72, 150), "Abstract", fontsize=14)
        page.insert_text((72, 175), "This paper evaluates a synthetic dataset.", fontsize=10)
        doc.save(pdf)
    monkeypatch.setattr(paper_render, "settings", lambda: SimpleNamespace(
        paper_render_dir=tmp_path / "state", pdf_root=tmp_path, pdf_cache_dir=tmp_path / "cache",
    ))
    monkeypatch.setattr(paper_render, "_item_detail", lambda _: {"pdf_path": str(pdf), "title": "Paper"})
    monkeypatch.setattr(paper_render, "_JOBS", {})
    return pdf


def test_offline_build_uses_local_pdf_without_creating_source(paper, monkeypatch):
    monkeypatch.setenv("ZS_OFFLINE", "1")
    monkeypatch.setattr(tex, "_download_capped", lambda _: pytest.fail("offline network attempt"))
    result = paper_render.build_paper_read("OFFLINE", allow_arxiv_source=True)
    assert result["status"] == "completed" and result["source_tier"] == "pdf"
    assert result["source_dir"] == ""
    assert not (Path(result["outputs"]["presentation"]).parent / "source").exists()


def test_worker_records_source_failure_and_retry_builds_current_tex(paper, monkeypatch):
    monkeypatch.setattr(tex, "_download_capped", lambda _: _archive())
    extract = tex._safe_extract

    def interrupted(tar, target):
        (target / "partial.tex").write_text("partial extraction")
        raise tarfile.TarError("interrupted source")

    monkeypatch.setattr(tex, "_safe_extract", interrupted)
    with pytest.raises(tarfile.TarError, match="interrupted source"):
        paper_render._build_job("RETRY", force=True, allow_arxiv_source=True, allow_acquire_missing=False)
    assert paper_render.render_paper("RETRY")["status"] == "error"
    assert not list(paper.parent.rglob("*.tex"))

    monkeypatch.setattr(tex, "_safe_extract", extract)
    result = paper_render.build_paper_read("RETRY", allow_arxiv_source=True)
    assert result["status"] == "completed" and result["source_tier"] == "arxiv_tex"
    assert (Path(result["source_dir"]) / "nested/main.tex").is_file()
    assert not list(paper.parent.rglob("partial.tex"))
    assert not paper_render.render_paper("RETRY").get("stale")
