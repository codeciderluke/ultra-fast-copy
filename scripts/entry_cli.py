"""PyInstaller entry point for the CLI.

The package modules use relative imports, which break when PyInstaller runs a
module directly as __main__. This launcher imports the package properly instead.
"""

from __future__ import annotations

import multiprocessing
import sys

from fast_transfer.cli.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # required in frozen builds
    sys.exit(main())
