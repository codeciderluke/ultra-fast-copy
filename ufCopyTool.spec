# PyInstaller spec for the GUI. Build: pyinstaller ufCopyTool.spec  ->  dist\ufCopyTool.exe
# Product name stays "Ultra Fast Copy"; only the executable file is ufCopyTool.exe.
from pathlib import Path

ROOT = Path(SPECPATH)
ICON = ROOT / "assets" / "app.ico"

a = Analysis(
    ["scripts/entry_gui.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    # PyQt6 pulls these in lazily; PyInstaller cannot see them statically.
    hiddenimports=["fast_transfer.integration.context_menu", "xxhash"],
    hookspath=[],
    runtime_hooks=[],
    # Qt modules this app never touches; excluding them cuts ~40 MB.
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.Qt3DCore",
        "PySide6.QtBluetooth",
        "PySide6.QtNetwork",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtDesigner",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ufCopyTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # no console window for the GUI
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.exists() else None,
    version="version_info_gui.txt" if (ROOT / "version_info_gui.txt").exists() else None,
)
