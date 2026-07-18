"""Explorer right-click menu entry, registered under HKCU (no admin needed).

Adds a verb for files, folders, folder backgrounds, and drives. Windows 11 shows
registry verbs under "Show more options" unless the app ships a packaged
IExplorerCommand; `install()` says so rather than pretending otherwise.
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path

from .. import APP_NAME, APP_SLUG
from ..utils.paths import IS_WINDOWS
from ..utils.system import local_app_data_dir

VERB = "UltraFastCopy"
MENU_TEXT = f"Open with {APP_NAME}"

# Where the verb is registered, and the argument Explorer substitutes:
#   %1 = the clicked file, %V = the clicked folder or the open folder
_TARGETS: tuple[tuple[str, str], ...] = (
    (r"Software\Classes\*\shell", "%1"),  # any file
    (r"Software\Classes\Directory\shell", "%V"),  # a folder
    (r"Software\Classes\Directory\Background\shell", "%V"),  # empty space in a folder
    (r"Software\Classes\Drive\shell", "%V"),  # a drive
)


@dataclass(slots=True, frozen=True)
class ShellStatus:
    """Where the verb is registered and what it runs."""

    installed: bool
    command: str = ""
    icon: str = ""
    locations: tuple[str, ...] = ()


def _winreg():  # type: ignore[no-untyped-def]
    if not IS_WINDOWS:
        raise OSError("The Explorer context menu is only available on Windows.")
    import winreg

    return winreg


def icon_path() -> Path:
    return local_app_data_dir(APP_SLUG) / "app.ico"


def _ensure_icon() -> Path:
    """Write the .ico the shell shows next to the menu entry."""
    target = icon_path()
    try:
        # Rendering needs a QApplication; reuse one if the GUI is already up.
        from PySide6.QtWidgets import QApplication

        from ..gui.icon import save_ico

        owns = QApplication.instance() is None
        app = QApplication([]) if owns else None
        try:
            save_ico(target)
        finally:
            if owns and app is not None:
                app.quit()
    except Exception:
        return target
    return target


def launch_command(argument: str) -> str:
    """The command Explorer runs, quoted for the registry.

    The menu must open the GUI. When invoked from the frozen CLI, sys.executable
    is ufCopy.exe, which has no --source flag, so prefer the GUI exe
    sitting next to it.
    """
    executable = Path(sys.executable)
    gui_exe = executable.parent / "ufCopyTool.exe"

    if getattr(sys, "frozen", False):
        target = gui_exe if gui_exe.exists() else executable
        return f'"{target}" --source "{argument}"'

    if gui_exe.exists():  # installed into a venv's Scripts directory
        return f'"{gui_exe}" --source "{argument}"'

    # Running from source: pythonw.exe avoids a console window flashing up.
    pythonw = executable.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else executable
    return f'"{interpreter}" -m fast_transfer.gui.app --source "{argument}"'


def install() -> ShellStatus:
    """Register the verb for the current user."""
    winreg = _winreg()
    icon = _ensure_icon()
    locations: list[str] = []
    command = ""

    for base, argument in _TARGETS:
        command = launch_command(argument)
        key_path = f"{base}\\{VERB}"
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, None, 0, winreg.REG_SZ, MENU_TEXT)
                if icon.exists():
                    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(icon))
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\command") as key:
                winreg.SetValueEx(key, None, 0, winreg.REG_SZ, command)
            locations.append(key_path)
        except OSError as exc:
            raise OSError(f"Could not write {key_path}: {exc}") from exc

    return ShellStatus(True, command=command, icon=str(icon), locations=tuple(locations))


def uninstall() -> ShellStatus:
    """Remove the verb. Missing keys are not an error."""
    winreg = _winreg()
    removed: list[str] = []

    for base, _argument in _TARGETS:
        key_path = f"{base}\\{VERB}"
        for suffix in ("\\command", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path + suffix)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise OSError(f"Could not remove {key_path}{suffix}: {exc}") from exc
        removed.append(key_path)

    return ShellStatus(False, locations=tuple(removed))


def status() -> ShellStatus:
    """Report whether the verb is registered and what it points at."""
    winreg = _winreg()
    found: list[str] = []
    command = ""
    icon = ""

    for base, _argument in _TARGETS:
        key_path = f"{base}\\{VERB}"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                found.append(key_path)
                with contextlib.suppress(FileNotFoundError):
                    icon = icon or winreg.QueryValueEx(key, "Icon")[0]
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\command") as key:
                command = command or winreg.QueryValueEx(key, None)[0]
        except FileNotFoundError:
            continue
        except OSError:
            continue

    return ShellStatus(bool(found), command=command, icon=icon, locations=tuple(found))


def stale(current: ShellStatus) -> bool:
    """True when the registered command points at a different install.

    Each verb embeds its own argument (%1 for files, %V for folders), so the
    reported command is compared against every valid form.
    """
    if not current.installed or not current.command:
        return False
    expected = {launch_command(argument) for _base, argument in _TARGETS}
    return current.command not in expected
