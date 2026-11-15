"""Application wiring: tray, hotkey, single instance, retention, first-run welcome."""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .core.models import HistoryFilter
from .core.store import HistoryStore
from .ocr.preprocess import prepare
from .ocr.service import OcrService
from .ocr.tesseract import TesseractEngine
from .platform.base import HotKeyCombo, HotKeyResult, PlatformBackend
from .services import ClipboardMonitor, ClipboardWriter
from .settings import Settings, data_dir
from .ui import icons
from .ui.controller import HistoryController
from .ui.panel import HistoryPanel
from .ui.settings_dialog import SettingsDialog
from .ui.theme import Theme, stylesheet

log = logging.getLogger("clipboard_memory")
SERVER_NAME = "ClipboardMemoryV2-single-instance"


def setup_logging(directory: Path) -> None:
    """Log file holds events and errors only. Clipboard contents are never logged."""
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(directory / "clipboard-memory.log", maxBytes=512_000, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def make_backend() -> PlatformBackend:
    if sys.platform == "win32":
        from .platform.windows import WindowsBackend
        return WindowsBackend()
    from .platform.dev import DevBackend
    return DevBackend()


def signal_running_instance() -> bool:
    """If another instance is running, ask it to show its window and return True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


class _Bridge(QObject):
    hotkeyPressed = Signal()


class ClipboardMemoryApp(QObject):
    pausedChanged = Signal(bool)

    def __init__(self, qapp: QApplication, backend: PlatformBackend | None = None, directory: Path | None = None,
                 engine=None):
        super().__init__()
        self.qapp = qapp
        self.data_dir = directory or data_dir()
        setup_logging(self.data_dir)
        self.settings = Settings.load(self.data_dir / "settings.json")
        self.backend = backend or make_backend()
        self.theme = Theme(self.settings.theme)
        self.theme.changed.connect(self.apply_app_style)
        self.apply_app_style()

        self.store = HistoryStore(self.data_dir)
        self.writer = ClipboardWriter(self.store, self.backend)
        self.bridge = _Bridge()
        self.controller = HistoryController(self.store, self.writer)
        self.ocr = OcrService(self.store, engine or TesseractEngine(), self.data_dir / "tmp",
                              enabled=lambda: self.settings.ocr_enabled,
                              on_update=lambda *_: self.controller.historyChanged.emit(),
                              preprocess=prepare)
        self.controller.ocr = self.ocr
        self.monitor = ClipboardMonitor(self.store, self.backend, self.settings, self.ocr,
                                        on_saved=lambda: self.controller.historyChanged.emit())
        self.panel = HistoryPanel(self.controller, self.theme, self.backend, self.settings)
        self.panel.openSettingsRequested.connect(self.show_settings)
        self.panel.hidden.connect(self.settings.save)
        self._settings_dialog: SettingsDialog | None = None
        self._hotkey_warning_shown = False

        self.bridge.hotkeyPressed.connect(self.panel.toggle)
        self._build_tray()
        self._start_server()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.ocr.start()
        self.monitor.start()
        self.backend.start(self.monitor.notify_changed, self.bridge.hotkeyPressed.emit)
        result = self.backend.register_hotkey(self.settings.hotkey)
        if not result.ok:
            self._report_hotkey_failure(result)
        self.ocr.requeue_pending()
        self._purge()
        self._purge_timer = QTimer(self)
        self._purge_timer.timeout.connect(self._purge)
        self._purge_timer.start(3_600_000)
        if not self.settings.did_show_welcome:
            self.settings.did_show_welcome = True
            self.settings.save()
            QTimer.singleShot(600, self._welcome)

    def shutdown(self) -> None:
        self.settings.save()
        self.monitor.stop()
        self.ocr.stop()
        self.backend.stop()
        self.tray.hide()
        self.store.close()

    def _purge(self) -> None:
        s = self.settings
        threading.Thread(target=lambda: self.store.purge(s.retention_days, s.max_entries), daemon=True).start()

    # ------------------------------------------------------------------ hotkey

    def apply_hotkey(self, combo: HotKeyCombo) -> HotKeyResult:
        result = self.backend.register_hotkey(combo)
        if result.ok:
            self.settings.set_hotkey(combo)
            self.settings.save()
            self._refresh_tray()
        return result

    def _report_hotkey_failure(self, result: HotKeyResult) -> None:
        log.warning("hotkey registration failed")
        box = QMessageBox(QMessageBox.Warning, "Clipboard Memory",
                          f"Couldn’t register the shortcut {self.settings.hotkey.display}",
                          QMessageBox.Ok)
        box.setInformativeText(result.message + "\n\nYou can still open history from the tray icon.")
        box.setWindowFlag(box.windowFlags().WindowStaysOnTopHint, True)
        QTimer.singleShot(0, box.show)
        self._hotkey_box = box

    # ------------------------------------------------------------------ tray

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(icons.app_icon(), self.qapp)
        self.menu = QMenu()
        self.open_action = self.menu.addAction("Open Clipboard History", self.panel.show_panel)
        self.pause_action = QAction("Pause Monitoring", self.menu)
        self.pause_action.triggered.connect(lambda: self.set_paused(not self.settings.paused))
        self.menu.addAction(self.pause_action)
        self.menu.addSeparator()
        self.menu.addAction("Settings…", self.show_settings)
        self.menu.addAction("Clear History…", self.confirm_clear_history)
        self.menu.addSeparator()
        self.menu.addAction("Quit Clipboard Memory", self.qapp.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda reason: self.panel.toggle() if reason == QSystemTrayIcon.Trigger else None)
        self._refresh_tray()
        self.tray.show()

    def _refresh_tray(self) -> None:
        paused = self.settings.paused
        self.open_action.setText(f"Open Clipboard History\t{self.settings.hotkey.display}")
        self.pause_action.setText("Resume Monitoring" if paused else "Pause Monitoring")
        self.tray.setIcon(icons.app_icon(paused))
        self.tray.setToolTip("Clipboard Memory — paused" if paused else f"Clipboard Memory — {self.settings.hotkey.display}")

    def set_paused(self, paused: bool) -> None:
        self.settings.paused = paused
        self.settings.save()
        self._refresh_tray()
        self.pausedChanged.emit(paused)

    def apply_app_style(self) -> None:
        self.qapp.setStyleSheet(stylesheet(self.theme.palette))

    # ------------------------------------------------------------------ dialogs

    def show_settings(self) -> None:
        self.panel.hide_panel()
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self)
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def confirm_clear_history(self) -> None:
        """Clears everything except pinned items. Pinned entries are always kept."""
        pinned = self.store.count(flt=HistoryFilter.PINNED)
        box = QMessageBox(QMessageBox.Question, "Clear history", "Clear clipboard history?")
        box.setInformativeText("Pinned items are kept" + (f" ({pinned})" if pinned else "") + ". This can’t be undone.")
        box.addButton("Clear", QMessageBox.AcceptRole)
        cancel = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel)
        box.setWindowFlag(box.windowFlags().WindowStaysOnTopHint, True)
        box.exec()
        if box.clickedButton() is not cancel and box.buttonRole(box.clickedButton()) == QMessageBox.AcceptRole:
            self.store.clear(keep_pinned=True)
            self.controller.historyChanged.emit()
            if self._settings_dialog:
                self._settings_dialog._refresh_storage()

    def _welcome(self) -> None:
        hk = self.settings.hotkey.display
        box = QMessageBox(QMessageBox.Information, "Welcome to Clipboard Memory",
                          f"Clipboard Memory is running in the notification area.")
        box.setInformativeText(
            f"Copy as usual with Ctrl+C, then press {hk} anywhere to search and restore anything you copied.\n\n"
            "Everything stays on this PC. Passwords and API keys are skipped by default (best-effort detection).\n\n"
            "Tip: Ctrl+Shift+V is also “paste as plain text” in several apps. If that gets in the way, "
            "change the shortcut in Settings.")
        box.setWindowFlag(box.windowFlags().WindowStaysOnTopHint, True)
        QTimer.singleShot(0, box.show)
        self._welcome_box = box

    # ------------------------------------------------------------------ single instance

    def _start_server(self) -> None:
        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._server.listen(SERVER_NAME)

    def _on_connection(self) -> None:
        sock = self._server.nextPendingConnection()
        if sock:
            sock.readyRead.connect(lambda s=sock: (s.readAll(), self.panel.show_panel(), s.disconnectFromServer()))
