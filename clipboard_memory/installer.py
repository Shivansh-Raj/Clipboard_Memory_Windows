"""Per-user install/uninstall of the packaged app. No administrator rights; nothing outside the user profile."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_FOLDER = "Clipboard Memory"
EXE_NAME = "ClipboardMemory.exe"


def _say(title: str, text: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(f"{title}: {text}")


def _paths() -> tuple[Path, Path]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return local / "Programs" / APP_FOLDER, appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _shortcut(menu_dir: Path, target: Path) -> None:
    """A Start Menu entry. An Internet Shortcut pointing at the exe needs no COM and no extra dependencies."""
    menu_dir.mkdir(parents=True, exist_ok=True)
    (menu_dir / "Clipboard Memory.url").write_text(
        f"[InternetShortcut]\nURL={target.as_uri()}\nIconFile={target}\nIconIndex=0\n", "utf-8")


def install() -> int:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        _say("Clipboard Memory", "--install only works from the packaged Windows app.")
        return 1
    src = Path(sys.executable).parent
    dest, menu = _paths()
    try:
        if src.resolve() != dest.resolve():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        exe = dest / EXE_NAME
        _shortcut(menu, exe)
        from .platform.windows import WindowsBackend
        err = WindowsBackend().set_autostart(True)
        subprocess.Popen([str(exe)], cwd=str(dest), creationflags=0x00000008 | 0x00000200)
    except OSError as e:
        _say("Clipboard Memory", f"Installation failed: {e.strerror or e}")
        return 1
    _say("Clipboard Memory", f"Installed to {dest}\n\nIt is now running in the notification area."
         + (f"\n\nNote: {err}" if err else "\n\nIt will start with Windows (change this in Settings)."))
    return 0


def uninstall() -> int:
    if sys.platform != "win32":
        return 1
    dest, menu = _paths()
    from .platform.windows import WindowsBackend
    WindowsBackend().set_autostart(False)
    (menu / "Clipboard Memory.url").unlink(missing_ok=True)
    if getattr(sys, "frozen", False):
        # The running exe can't delete itself: hand the folder to a short-lived cmd that waits, then removes it.
        subprocess.Popen(["cmd", "/c", "ping -n 3 127.0.0.1 >nul & rmdir /s /q \"%s\"" % dest],
                         creationflags=0x08000000)
    _say("Clipboard Memory", "Clipboard Memory was removed.\n\nYour clipboard history in "
         "%APPDATA%\\Clipboard Memory was kept; delete that folder to remove it too.")
    return 0
