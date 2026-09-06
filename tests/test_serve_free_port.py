"""Serving never terminates another process to obtain its port."""
import socket

import pytest

from zotero_summarizer.cli import build_parser


def test_serve_preserves_existing_listener(monkeypatch):
    import uvicorn

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        def run(_app, **kwargs):
            assert kwargs["port"] == port
            with socket.socket() as attempt:
                attempt.bind((kwargs["host"], port))

        monkeypatch.setattr(uvicorn, "run", run)
        args = build_parser().parse_args(["serve", "--port", str(port)])
        with pytest.raises(OSError):
            args.func(args)
        assert listener.getsockname()[1] == port
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--no-kill"])
