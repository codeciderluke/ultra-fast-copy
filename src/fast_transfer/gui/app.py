"""`ufCopyTool` GUI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .. import APP_NAME, APP_SLUG, __version__
from ..config.settings import load_settings
from ..utils.logging import configure_logging
from ..utils.paths import IS_WINDOWS
from .icon import app_icon
from .main_window import MainWindow
from .theme import apply_theme


def _set_windows_app_id() -> None:
    """Give Windows an explicit AppUserModelID.

    Without this the taskbar groups the app under `python.exe` and shows its
    icon instead of ours.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"CodeciderLab.{APP_SLUG}.{__version__}"
        )
    except Exception:
        pass


def create_app(argv: list[str] | None = None) -> QApplication:
    """Build the QApplication with theme and icon applied."""
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("CodeciderLab")
    apply_theme(app)
    app.setWindowIcon(app_icon())
    return app


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the launch arguments the Explorer context menu passes in."""
    parser = argparse.ArgumentParser(prog="ufCopyTool", description=APP_NAME)
    parser.add_argument("paths", nargs="*", type=Path, help="Files or folders to use as the source.")
    parser.add_argument("--source", type=Path, default=None, help="Source path.")
    parser.add_argument("--destination", type=Path, default=None, help="Destination path.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Launch the GUI. Returns the process exit code."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    _set_windows_app_id()
    settings = load_settings()
    configure_logging(level=settings.logging.level, retention_days=settings.logging.retention_days)

    # A path from the context menu wins over the remembered one.
    source = args.source or (args.paths[0] if args.paths else None)
    if source is not None:
        settings.ui.last_source = str(source)
    if args.destination is not None:
        settings.ui.last_destination = str(args.destination)

    app = create_app()
    window = MainWindow(settings)
    if source is not None:
        window.preselect(source)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
