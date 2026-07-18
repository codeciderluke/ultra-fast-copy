# PyInstaller spec for the Setup program.
# Build AFTER ufCopy.exe and ufCopyTool.exe exist in dist\ -- it bundles them.
#   pyinstaller UltraFastCopy-Setup.spec  ->  dist\UltraFastCopy-Setup.exe
from pathlib import Path

ROOT = Path(SPECPATH)
ICON = ROOT / "assets" / "app.ico"

a = Analysis(
    ["scripts/installer.py"],
    pathex=["src"],
    binaries=[],
    # The two application executables ride inside the installer as data.
    datas=[
        ("dist/ufCopy.exe", "."),
        ("dist/ufCopyTool.exe", "."),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # The installer is pure stdlib; no Qt in the installer's own code.
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
    name="UltraFastCopy-Setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.exists() else None,
    version="version_info_cli.txt" if (ROOT / "version_info_cli.txt").exists() else None,
)
