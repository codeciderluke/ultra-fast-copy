"""Setup program for Ultra Fast Copy.

Double-clicking the built `UltraFastCopy-Setup.exe` installs both executables to
a per-user location and registers the Explorer right-click menu, so any file,
folder, or drive can be opened with Ultra Fast Copy from its context menu. No
administrator rights are needed -- everything goes under the current user.

The installer reuses the application's own, tested menu registration: after
copying the executables it runs `ufCopy.exe shell install`, which points the
verb at the freshly installed `ufCopyTool.exe`.

Build: bundled into UltraFastCopy-Setup.exe by UltraFastCopy-Setup.spec.
Run:   UltraFastCopy-Setup.exe            (install)
       UltraFastCopy-Setup.exe /uninstall (remove)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Ultra Fast Copy"
APP_DIRNAME = "UltraFastCopy"
EXES = ("ufCopy.exe", "ufCopyTool.exe")


def _source_dir() -> Path:
    """Where the bundled executables sit: the PyInstaller temp dir when frozen,
    otherwise the repo's dist/ folder for a source run."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent / "dist"


def _install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / APP_DIRNAME


def _start_menu_lnk() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def _make_shortcut(target: Path) -> None:
    """Create a Start Menu shortcut via WScript.Shell (no pywin32 needed)."""
    lnk = _start_menu_lnk()
    ps = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        f"$s.TargetPath='{target}';$s.WorkingDirectory='{target.parent}';"
        f"$s.IconLocation='{target}';$s.Description='{APP_NAME}';$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=True, capture_output=True,
        )
        print(f"  Start Menu shortcut : {lnk}")
    except Exception as exc:  # noqa: BLE001 - a missing shortcut must not fail the install
        print(f"  (Start Menu shortcut skipped: {exc})")


def install() -> int:
    src = _source_dir()
    dst = _install_dir()
    print(f"Installing {APP_NAME} to:\n  {dst}\n")

    missing = [e for e in EXES if not (src / e).exists()]
    if missing:
        print(f"ERROR: bundled executable(s) not found: {', '.join(missing)}")
        return 1

    dst.mkdir(parents=True, exist_ok=True)
    for exe in EXES:
        shutil.copy2(src / exe, dst / exe)
        print(f"  installed {exe}")

    # Register the Explorer right-click menu using the app's own logic. The
    # installed CLI resolves the GUI exe sitting next to it, so the verb points
    # at this install.
    print("\nRegistering the Explorer right-click menu ...")
    result = subprocess.run([str(dst / "ufCopy.exe"), "shell", "install"], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  WARNING: menu registration returned {result.returncode}\n{result.stdout}{result.stderr}")
    else:
        print("  registered for the current user (no admin needed).")

    _make_shortcut(dst / "ufCopyTool.exe")

    print(
        f"\n{APP_NAME} is installed.\n"
        "Right-click any file, folder, or drive and choose\n"
        f'  "Open with {APP_NAME}"\n'
        "(On Windows 11 it is under \"Show more options\", Shift+F10.)\n"
    )
    return 0


def uninstall() -> int:
    dst = _install_dir()
    print(f"Removing {APP_NAME} from:\n  {dst}\n")

    cli = dst / "ufCopy.exe"
    if cli.exists():
        print("Unregistering the Explorer right-click menu ...")
        subprocess.run([str(cli), "shell", "uninstall"], capture_output=True, text=True)

    lnk = _start_menu_lnk()
    if lnk.exists():
        lnk.unlink(missing_ok=True)
        print(f"  removed shortcut {lnk}")

    shutil.rmtree(dst, ignore_errors=True)
    print(f"\n{APP_NAME} has been uninstalled.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="UltraFastCopy-Setup", description=f"{APP_NAME} setup")
    # Accept both "/uninstall" (Windows style) and "--uninstall".
    argv = ["--uninstall" if a.lower() in ("/uninstall", "/u") else a for a in sys.argv[1:]]
    parser.add_argument("--uninstall", action="store_true", help="Remove the installation.")
    parser.add_argument("--silent", action="store_true", help="Do not wait for a keypress at the end.")
    args = parser.parse_args(argv)

    try:
        code = uninstall() if args.uninstall else install()
    except Exception as exc:  # noqa: BLE001 - surface any failure to the console user
        print(f"\nSetup failed: {exc}")
        code = 1

    if not args.silent:
        try:
            input("\nPress Enter to close ...")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
