"""Run the existing app behind an explicitly configured Tailscale Serve gateway."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import stat

from zotero_summarizer.settings import Settings


DEFAULT_SHUTDOWN_SECONDS = 30
MAX_SHUTDOWN_SECONDS = 120


def _socket_directory(settings: Settings) -> Path:
    if os.name != "posix":
        raise ValueError("Mobile gateway requires POSIX private Unix sockets")
    root, data = settings.project_root.absolute(), settings.data_dir.absolute()
    if data.parent != root or root.resolve() != root or data.resolve() != data:
        raise ValueError("Mobile gateway requires canonical selected project/data directories, not symlinks")
    uid = os.getuid()
    for ancestor in (root, *root.parents):
        info = ancestor.lstat()
        writable = info.st_mode & 0o022
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid)
                or (writable and not info.st_mode & stat.S_ISVTX)):
            raise ValueError("Mobile gateway requires trusted, non-writable or sticky project ancestry")
    root_info = root.stat()
    if root_info.st_uid != uid or root_info.st_mode & 0o022:
        raise ValueError("Mobile gateway requires an owner-controlled project without group/world writes")
    data.mkdir(mode=0o700, exist_ok=True)
    info = data.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
        raise ValueError("Mobile gateway requires owner-controlled data without group/world writes")
    directory = data / "mobile"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Mobile socket directory must be an owner-only 0700 directory, not a symlink")
    return directory


def _remove_owned_socket(path: Path, identity: tuple[int, int]) -> None:
    if path.exists() or path.is_symlink():
        current = path.lstat()
        if (current.st_dev, current.st_ino) != identity or not stat.S_ISSOCK(current.st_mode):
            raise RuntimeError("Mobile socket path changed; replacement was preserved")
        path.unlink()


@contextmanager
def private_listener(settings: Settings):
    path = _socket_directory(settings) / "api.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    identity = None
    released = False

    def release_path():
        nonlocal released
        if not released:
            _remove_owned_socket(path, identity)
            released = True

    try:
        # The directory is private before bind; there is no public chmod window.
        listener.bind(str(path))
        info = path.lstat()
        identity = (info.st_dev, info.st_ino)
        path.chmod(0o600)
        yield listener, path, release_path
    finally:
        try:
            if identity is not None:
                release_path()
        finally:
            listener.close()


def _shutdown_seconds() -> int:
    value = int(os.environ.get("ZS_MOBILE_SHUTDOWN_SECONDS", DEFAULT_SHUTDOWN_SECONDS))
    if not 0 < value <= MAX_SHUTDOWN_SECONDS:
        raise ValueError(f"ZS_MOBILE_SHUTDOWN_SECONDS must be in 1..{MAX_SHUTDOWN_SECONDS}")
    return value


def _serve_mobile(args: argparse.Namespace) -> int:
    from zotero_summarizer.api.mobile_access import MobileAccessPolicy

    settings = Settings.load(project_root=args.project_root)
    path = settings.data_dir.absolute() / "mobile" / "api.sock"
    policy = MobileAccessPolicy(args.origin, args.owner_login, path)
    shutdown_seconds = _shutdown_seconds()
    import uvicorn
    from zotero_summarizer.api.app import create_app

    class GatewayServer(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            # The outer capture owns signals for the entire socket lifetime.
            yield

    config = uvicorn.Config(
        create_app(settings, mobile_access=policy), uds=str(path),
        proxy_headers=False, workers=1, http="h11", access_log=False,
        timeout_graceful_shutdown=shutdown_seconds,
    )
    server = GatewayServer(config)
    with uvicorn.Server.capture_signals(server):
        with private_listener(settings) as (listener, _path, _release_path):
            if not server.should_exit:
                server.run(sockets=[listener])
    return 0 if server.started else 3


def register_mobile_serve(subparsers: argparse._SubParsersAction) -> None:
    command = subparsers.add_parser(
        "serve-mobile", help="Serve through a private Unix socket + owner-authenticated Tailscale HTTPS"
    )
    command.add_argument("--origin", required=True, help="Exact HTTPS .ts.net origin served by Tailscale Serve")
    command.add_argument("--owner-login", required=True, help="Exact allowed owner login (not an API key)")
    command.add_argument("--project-root", default=None)
    command.set_defaults(func=_serve_mobile)
