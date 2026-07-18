"""Render assets/app.ico from the drawn icon. Run before building."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_transfer.gui.icon import save_ico, save_png  # noqa: E402


def main() -> int:
    app = QApplication([])  # QPainter needs an application instance
    assets = ROOT / "assets"
    ico = save_ico(assets / "app.ico")
    png = save_png(assets / "app.png", 256)
    app.quit()
    print(f"wrote {ico} ({ico.stat().st_size:,} bytes)")
    print(f"wrote {png} ({png.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
