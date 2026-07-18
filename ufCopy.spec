# PyInstaller spec for the CLI. Build: pyinstaller ufCopy.spec  ->  dist\ufCopy.exe
# Product name stays "Ultra Fast Copy"; only the executable file is ufCopy.exe.
from pathlib import Path

ROOT = Path(SPECPATH)
ICON = ROOT / "assets" / "app.ico"

a = Analysis(
    ["scripts/entry_cli.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=["xxhash"],
    hookspath=[],
    runtime_hooks=[],
    # The CLI never builds a window, so Qt is dead weight here. `ufCopy gui`
    # is unavailable in this build by design -- ship ufCopyTool.exe for that.
    # Excluding PySide6 keeps the CLI small and free of the LGPL Qt bundle.
    excludes=["PySide6", "shiboken6", "tkinter", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ufCopy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.exists() else None,
    version="version_info_cli.txt" if (ROOT / "version_info_cli.txt").exists() else None,
)
