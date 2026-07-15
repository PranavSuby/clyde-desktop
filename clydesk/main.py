"""Clyde Desktop entry point.

    python -m clydesk.main            # native desktop window (pywebview)
    python -m clydesk.main --browser  # serve at http://localhost:8420
"""

import argparse
import os
import secrets
import sys

from nicegui import app, ui

from . import ui_page
from .config import DATA_DIR, IMAGES_DIR

app.add_static_files("/images", IMAGES_DIR)
ui_page.build()


def _native_available() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        return False


def _storage_secret() -> str:
    """A stable per-user secret for signing NiceGUI session storage.

    Generated once and stored 0600 under the data dir; not a shared constant
    that could be forged."""
    path = os.path.join(DATA_DIR, "storage_secret")
    try:
        with open(path) as f:
            val = f.read().strip()
            if val:
                return val
    except OSError:
        pass
    os.makedirs(DATA_DIR, exist_ok=True)
    val = secrets.token_hex(32)
    try:
        # O_EXCL: if two instances start at once, only the first creates the
        # file; the loser reads the winner's secret rather than clobbering it,
        # so already-signed sessions stay verifiable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path) as f:
            return f.read().strip() or val
    with os.fdopen(fd, "w") as f:
        f.write(val)
    return val


if __name__ in {"__main__", "__mp_main__"}:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true",
                        help="serve in the browser instead of a native window")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address in browser mode (default: "
                             "127.0.0.1, loopback only). Pass 0.0.0.0 to "
                             "deliberately expose the app on your LAN.")
    args, _ = parser.parse_known_args()

    native = not args.browser and _native_available()
    if not args.browser and not native:
        print("pywebview not available — falling back to browser mode",
              file=sys.stderr)

    # Loopback by default: browser mode otherwise binds every interface, which
    # would let anyone on the LAN drive the agent (run skills, actuate the
    # lamp, read indexed files) with no authentication.
    host = args.host
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding {host} exposes Clyde on the network with no "
              f"authentication — anyone who can reach this port can use the "
              f"agent.", file=sys.stderr)
    if not native:
        print(f"Clyde Desktop: http://{host}:{args.port}")
    # storage_secret enables signed session storage; a fixed dev value would
    # be forgeable, so derive a per-user one and persist it.
    ui.run(
        title="Clyde",
        native=native,
        host=host,
        window_size=(1280, 860) if native else None,
        port=args.port,
        show=False,
        reload=False,
        dark=True,
        storage_secret=_storage_secret(),
    )
