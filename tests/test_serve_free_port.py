"""Serving never terminates another process to obtain its port."""
import socket

import pytest

from zotero_summarizer.cli import build_parser


def test_serve_preserves_existing_listener(monkeypatch, tmp_path):
    import uvicorn

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        def run(_app, **_kwargs):
            raise AssertionError("a busy port must fail before uvicorn/lifespan startup")

        monkeypatch.setattr(uvicorn, "run", run)
        args = build_parser().parse_args(["serve", "--port", str(port)])
        args.project_root = str(tmp_path)  # main() supplies the admitted root.
        monkeypatch.setenv("ZOTERO_SUMMARIZER_HOME", str(tmp_path))
        assert args.func(args) == 1
        assert listener.getsockname()[1] == port
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--no-kill"])
