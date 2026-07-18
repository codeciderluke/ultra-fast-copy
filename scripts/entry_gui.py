"""PyInstaller entry point for the GUI. See entry_cli.py for why this exists."""

from __future__ import annotations

import multiprocessing
import sys

from fast_transfer.gui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
