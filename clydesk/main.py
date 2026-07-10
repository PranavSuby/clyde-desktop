"""Clyde Desktop entry point.

    python -m clydesk.main            # native desktop window (pywebview)
    python -m clydesk.main --browser  # serve at http://localhost:8420
"""

import argparse
import sys

from nicegui import app, ui

from . import ui_page
from .config import IMAGES_DIR

app.add_static_files("/images", IMAGES_DIR)
ui_page.build()


def _native_available() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ in {"__main__", "__mp_main__"}:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true",
                        help="serve in the browser instead of a native window")
    parser.add_argument("--port", type=int, default=8420)
    args, _ = parser.parse_known_args()

    native = not args.browser and _native_available()
    if not args.browser and not native:
        print("pywebview not available — falling back to browser mode",
              file=sys.stderr)

    if not native:
        print(f"Clyde Desktop: http://localhost:{args.port}")
    ui.run(
        title="Clyde",
        native=native,
        window_size=(1280, 860) if native else None,
        port=args.port,
        show=False,
        reload=False,
        dark=True,
    )
