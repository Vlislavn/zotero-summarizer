"""Run the existing app behind an explicitly configured Tailscale Serve gateway."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import stat

from zotero_summarizer.settings import Settings


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Mobile socket directory must be an owner-only 0700 directory, not a symlink")


def _remove_owned_socket(path: Path, identity: tuple[int, int]) -> None:
    if path.exists() or path.is_symlink():
        current = path.lstat()
        if (current.st_dev, current.st_ino) != identity or not stat.S_ISSOCK(current.st_mode):
            raise RuntimeError("Mobile socket path changed; replacement was preserved")
        path.unlink()


@contextmanager
def private_listener(settings: Settings):
    path = settings.data_dir.absolute() / "mobile" / "api.sock"
    _private_directory(path.parent)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    identity = None
    try:
        # The directory is already private before bind; there is no public chmod window.
        listener.bind(str(path))
        info = path.lstat()
        identity = (info.st_dev, info.st_ino)
        path.chmod(0o600)
        yield listener, path, lambda: _remove_owned_socket(path, identity)
    finally:
        listener.close()
        if identity is not None:
            _remove_owned_socket(path, identity)


def _serve_mobile(args: argparse.Namespace) -> int:
    from zotero_summarizer.api.mobile_access import MobileAccessPolicy

    settings = Settings.load(project_root=args.project_root)
    path = settings.data_dir.absolute() / "mobile" / "api.sock"
    policy = MobileAccessPolicy(args.origin, args.owner_login, path)
    import uvicorn
    from zotero_summarizer.api.app import create_app

    with private_listener(settings) as (listener, _path, release_path):
        class GatewayServer(uvicorn.Server):
            @contextmanager
            def capture_signals(self):
                with super().capture_signals():
                    try:
                        yield
                    finally:
                        release_path()

        config = uvicorn.Config(create_app(settings, mobile_access=policy), uds=str(path),
                                proxy_headers=False, workers=1, access_log=False)
        server = GatewayServer(config)
        server.run(sockets=[listener])
        return 0 if server.started else 3


def register_mobile_serve(subparsers: argparse._SubParsersAction) -> None:
    command = subparsers.add_parser("serve-mobile", help="Serve through private Unix socket + owner-authenticated Tailscale HTTPS")
    command.add_argument("--origin", required=True, help="Exact HTTPS .ts.net origin served by Tailscale Serve")
    command.add_argument("--owner-login", required=True, help="Exact allowed Tailscale owner login (not an API key)")
    command.add_argument("--project-root", default=None)
    command.set_defaults(func=_serve_mobile)
