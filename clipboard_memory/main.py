"""Entry point. Flags: --background (start hidden, used at login), --selftest-* (CI checks), --install/--uninstall."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from . import __version__


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--install", "--uninstall"):
        from .installer import install, uninstall
        return install() if argv[0] == "--install" else uninstall()
    if argv and argv[0].startswith("--selftest"):
        from .selftest import run
        return run(argv)

    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName("Clipboard Memory")
    qapp.setApplicationVersion(__version__)
    qapp.setQuitOnLastWindowClosed(False)

    from .app import ClipboardMemoryApp, signal_running_instance
    if signal_running_instance():
        return 0
    app = ClipboardMemoryApp(qapp)
    from .ui import icons
    qapp.setWindowIcon(icons.app_icon())
    app.start()
    code = qapp.exec()
    app.shutdown()
    return code
