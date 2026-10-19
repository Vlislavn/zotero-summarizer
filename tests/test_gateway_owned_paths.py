"""Owner-path and launcher failure contracts on disposable POSIX state only."""
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
import socket
import stat
import tempfile

import pytest

from zotero_summarizer.cli import _mobile_serve as mobile
from zotero_summarizer.settings import Settings


@pytest.fixture
def selected():
    with tempfile.TemporaryDirectory(prefix="zsg-", dir="/tmp") as directory:
        yield Settings.load(project_root=directory)


@pytest.mark.parametrize("part,mode", [("root", 0o777), ("root", 0o1777), ("data", 0o775), ("mobile", 0o755)])
def test_unsafe_directory_modes_refuse_without_repair_or_socket(selected, part, mode):
    root, data = selected.project_root, selected.data_dir
    data.mkdir(mode=0o700)
    leaf = data / "mobile"
    leaf.mkdir(mode=0o700)
    path = {"root": root, "data": data, "mobile": leaf}[part]
    path.chmod(mode)
    with pytest.raises(ValueError):
        with mobile.private_listener(selected):
            pytest.fail("unsafe directory accepted")
    assert stat.S_IMODE(path.stat().st_mode) == mode
    assert not (leaf / "api.sock").exists()


@pytest.mark.parametrize("part", ["data", "mobile"])
def test_symlinked_state_directory_is_not_followed(selected, part):
    outside = selected.project_root / "outside"
    outside.mkdir(mode=0o700)
    if part == "data":
        path = selected.data_dir
    else:
        selected.data_dir.mkdir(mode=0o700)
        path = selected.data_dir / "mobile"
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        with mobile.private_listener(selected):
            pytest.fail("symlink accepted")
    assert list(outside.iterdir()) == []
    assert path.is_symlink()


def test_untrusted_ancestor_refuses_without_making_state(selected):
    container = selected.project_root / "shared"
    container.mkdir(mode=0o777)
    container.chmod(0o777)
    root = container / "project"
    root.mkdir(mode=0o700)
    settings = replace(selected, project_root=root, data_dir=root / "data")
    with pytest.raises(ValueError):
        with mobile.private_listener(settings):
            pytest.fail("writable ancestor accepted")
    assert not settings.data_dir.exists()


def test_private_selected_project_inside_trusted_sticky_ancestor_is_supported(selected):
    ancestor = selected.project_root / "sticky"
    ancestor.mkdir(mode=0o700)
    ancestor.chmod(0o1777)
    root = ancestor / "private-project"
    root.mkdir(mode=0o700)
    settings = replace(selected, project_root=root, data_dir=root / "data")
    with mobile.private_listener(settings) as (_listener, path, _release):
        assert path.is_socket()
    assert not path.exists()


def test_existing_foreign_socket_and_symlink_are_preserved(selected):
    directory = selected.data_dir / "mobile"
    directory.mkdir(parents=True, mode=0o700)
    path = directory / "api.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as foreign:
        foreign.bind(str(path))
        identity = path.stat().st_ino
        with pytest.raises(OSError):
            with mobile.private_listener(selected):
                pytest.fail("existing socket replaced")
        assert path.stat().st_ino == identity
    path.unlink()
    marker = selected.project_root / "foreign"
    marker.write_bytes(b"keep foreign bytes")
    path.symlink_to(marker)
    with pytest.raises(OSError):
        with mobile.private_listener(selected):
            pytest.fail("existing symlink replaced")
    assert path.is_symlink() and marker.read_bytes() == b"keep foreign bytes"


def test_owned_cleanup_is_idempotent_and_closes_listener(selected):
    with mobile.private_listener(selected) as (listener, path, release):
        release()
        release()
        assert not path.exists()
    assert listener.fileno() == -1


def test_acknowledged_release_never_reinspects_a_new_listener(selected):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as successor:
        with mobile.private_listener(selected) as (listener, path, release):
            release()
            successor.bind(str(path))
            identity = path.stat().st_ino
            release()
        assert listener.fileno() == -1
        assert successor.fileno() != -1
        assert path.is_socket() and path.stat().st_ino == identity


def test_replacement_is_preserved_and_owned_listener_closed(selected):
    with pytest.raises(RuntimeError, match="replacement was preserved"):
        with mobile.private_listener(selected) as (listener, path, _release):
            path.unlink()
            path.write_bytes(b"replacement")
    assert path.read_bytes() == b"replacement"
    assert listener.fileno() == -1


def test_chmod_failure_closes_listener_and_removes_only_owned_socket(selected, monkeypatch):
    original = Path.chmod

    def refuse_socket(path, mode, **kwargs):
        if path.name == "api.sock":
            raise PermissionError("fixture chmod failure")
        return original(path, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", refuse_socket)
    with pytest.raises(PermissionError, match="fixture chmod failure"):
        with mobile.private_listener(selected):
            pytest.fail("chmod failure ignored")
    assert not (selected.data_dir / "mobile" / "api.sock").exists()


@pytest.mark.parametrize("outcome", ["error", "not_started", "stopped"])
def test_server_failure_or_normal_return_always_releases_owned_socket(selected, monkeypatch, outcome):
    import uvicorn

    captured = {}

    class Server:
        started = outcome == "stopped"

        def __init__(self, config):
            captured["config"] = config

        def run(self, *, sockets):
            captured["listener"] = sockets[0]
            captured["path"] = Path(sockets[0].getsockname())
            if outcome == "error":
                raise RuntimeError("fixture startup failure")

    monkeypatch.setattr(uvicorn, "Server", Server)
    args = Namespace(project_root=selected.project_root, origin="https://reader.example.ts.net", owner_login="owner@example.net")
    if outcome == "error":
        with pytest.raises(RuntimeError, match="fixture startup failure"):
            mobile._serve_mobile(args)
    else:
        assert mobile._serve_mobile(args) == (0 if outcome == "stopped" else 3)
    assert not captured["path"].exists()
    assert captured["listener"].fileno() == -1
    config = captured["config"]
    assert config.http == "h11" and config.proxy_headers is False
    assert config.timeout_graceful_shutdown == mobile.DEFAULT_SHUTDOWN_SECONDS


@pytest.mark.parametrize("value", ["0", "121", "bad", "-1"])
def test_shutdown_budget_is_bounded_and_fail_fast(value, monkeypatch):
    monkeypatch.setenv("ZS_MOBILE_SHUTDOWN_SECONDS", value)
    with pytest.raises(ValueError):
        mobile._shutdown_seconds()


def test_shutdown_budget_accepts_explicit_in_range_value(monkeypatch):
    monkeypatch.setenv("ZS_MOBILE_SHUTDOWN_SECONDS", "7")
    assert mobile._shutdown_seconds() == 7
