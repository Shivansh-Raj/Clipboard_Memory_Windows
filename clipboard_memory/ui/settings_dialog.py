"""Settings window (mirrors V1's SettingsView tabs, adapted for Windows)."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeyEvent
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from ..platform.base import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, HotKeyCombo
from .panel import human_size
from .theme import ui_font

if TYPE_CHECKING:
    from ..app import ClipboardMemoryApp


def _qt_key_to_vk(ev: QKeyEvent) -> int:
    vk = ev.nativeVirtualKey()
    if vk:
        return vk
    k = ev.key()
    if Qt.Key_A <= k <= Qt.Key_Z or Qt.Key_0 <= k <= Qt.Key_9:
        return k                                    # VK codes for letters/digits equal their ASCII codes
    if Qt.Key_F1 <= k <= Qt.Key_F24:
        return 0x70 + (k - Qt.Key_F1)
    return {Qt.Key_Space: 0x20, Qt.Key_Return: 0x0D, Qt.Key_Tab: 0x09}.get(k, 0)


_NAMES = {0x20: "Space", 0x0D: "Enter", 0x09: "Tab", 0x2D: "Insert", 0x2E: "Delete", 0x24: "Home", 0x23: "End",
          0x21: "PageUp", 0x22: "PageDown", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
          0xC0: "`", 0xBD: "-", 0xBB: "=", 0xDB: "[", 0xDD: "]", 0xBA: ";", 0xDE: "'", 0xBC: ",", 0xBE: ".", 0xBF: "/"}


def format_combo(vk: int, modifiers: int) -> str:
    parts = []
    if modifiers & MOD_CONTROL:
        parts.append("Ctrl")
    if modifiers & MOD_ALT:
        parts.append("Alt")
    if modifiers & MOD_SHIFT:
        parts.append("Shift")
    if modifiers & MOD_WIN:
        parts.append("Win")
    if 0x70 <= vk <= 0x87:
        parts.append(f"F{vk - 0x6F}")
    elif vk in _NAMES:
        parts.append(_NAMES[vk])
    else:
        parts.append(chr(vk).upper())
    return "+".join(parts)


class ShortcutRecorder(QPushButton):
    """Click, then press the new combination. It must include Ctrl, Alt or Win."""

    def __init__(self, combo: HotKeyCombo, on_change):
        super().__init__(combo.display)
        self.combo, self._on_change, self._recording = combo, on_change, False
        self.setProperty("plain", True)
        self.setMinimumWidth(170)
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(self._start)

    def set_combo(self, combo: HotKeyCombo) -> None:
        self.combo = combo
        self.setText(combo.display)

    def _start(self) -> None:
        self._recording = True
        self.setText("Press the new shortcut…")
        self.setFocus()
        self.grabKeyboard()

    def _stop(self) -> None:
        self._recording = False
        self.releaseKeyboard()
        self.setText(self.combo.display)

    def focusOutEvent(self, ev) -> None:
        if self._recording:
            self._stop()
        super().focusOutEvent(ev)

    def keyPressEvent(self, ev: QKeyEvent) -> None:
        if not self._recording:
            return super().keyPressEvent(ev)
        if ev.key() == Qt.Key_Escape:
            self._stop()
            return
        if ev.key() in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        m = ev.modifiers()
        mods = ((MOD_CONTROL if m & Qt.ControlModifier else 0) | (MOD_ALT if m & Qt.AltModifier else 0)
                | (MOD_SHIFT if m & Qt.ShiftModifier else 0) | (MOD_WIN if m & Qt.MetaModifier else 0))
        vk = _qt_key_to_vk(ev)
        if not vk or not (mods & (MOD_CONTROL | MOD_ALT | MOD_WIN)):
            self.setText("Include Ctrl, Alt or Win…")
            return
        combo = HotKeyCombo(vk, mods, format_combo(vk, mods))
        self._stop()
        self._on_change(combo)


class SettingsDialog(QDialog):
    def __init__(self, app: "ClipboardMemoryApp"):
        super().__init__(None)
        self.app = app
        self.s = app.settings
        self.setWindowTitle("Clipboard Memory Settings")
        self.setMinimumSize(560, 520)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        v = QVBoxLayout(self)
        tabs = QTabWidget()
        v.addWidget(tabs)
        tabs.addTab(self._general(), "General")
        tabs.addTab(self._history(), "History")
        tabs.addTab(self._privacy(), "Privacy")
        tabs.addTab(self._images(), "Images")
        close = QPushButton("Done")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        v.addLayout(row)

    # ---- helpers
    @staticmethod
    def _note(text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("muted")
        l.setWordWrap(True)
        return l

    def _combo(self, items: list[tuple[str, object]], current, setter) -> QComboBox:
        cb = QComboBox()
        for label, value in items:
            cb.addItem(label, value)
        i = cb.findData(current)
        cb.setCurrentIndex(max(0, i))
        cb.currentIndexChanged.connect(lambda _: setter(cb.currentData()))
        return cb

    def _set(self, name: str, value) -> None:
        setattr(self.s, name, value)
        self.s.save()

    # ---- tabs
    def _general(self) -> QWidget:
        w = QWidget()
        f = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Open clipboard history"))
        row.addStretch()
        self.recorder = ShortcutRecorder(self.s.hotkey, self._change_hotkey)
        row.addWidget(self.recorder)
        f.addLayout(row)
        self.hotkey_msg = self._note("Click the shortcut, then press the new key combination (it must include Ctrl, Alt or Win). "
                                     "Note: Ctrl+Shift+V is “paste as plain text” in many apps (Chrome, Edge, VS Code, Slack, "
                                     "Terminal). While Clipboard Memory runs, this shortcut opens history instead. "
                                     "Ctrl+Alt+V is a good alternative.")
        f.addWidget(self.hotkey_msg)
        f.addSpacing(8)
        login = QCheckBox("Launch at Windows startup")
        login.setChecked(self.app.backend.autostart_enabled())
        self.login_msg = self._note("")
        login.toggled.connect(lambda on: self._toggle_login(login, on))
        f.addWidget(login)
        f.addWidget(self.login_msg)
        pause = QCheckBox("Pause clipboard monitoring")
        pause.setChecked(self.s.paused)
        pause.toggled.connect(self.app.set_paused)
        self.app.pausedChanged.connect(pause.setChecked)
        f.addWidget(pause)
        f.addSpacing(8)
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Appearance"))
        trow.addStretch()
        trow.addWidget(self._combo([("Match Windows", "system"), ("Light", "light"), ("Dark", "dark")],
                                   self.s.theme, self._set_theme))
        f.addLayout(trow)
        f.addSpacing(8)
        f.addWidget(self._note("In the history window:\nEnter copy · Ctrl+Enter copy & close · Alt+Enter copy as plain text\n"
                               "Ctrl+P pin · Ctrl+D favourite · Del delete · Ctrl+F search · Tab next filter · "
                               "Ctrl+1–8 pick filter · Esc close"))
        f.addStretch()
        return w

    def _set_theme(self, mode: str) -> None:
        self._set("theme", mode)
        self.app.theme.set_mode(mode)
        self.app.apply_app_style()

    def _toggle_login(self, box: QCheckBox, on: bool) -> None:
        err = self.app.backend.set_autostart(on)
        self.login_msg.setText(err or "")
        if err:
            box.blockSignals(True)
            box.setChecked(self.app.backend.autostart_enabled())
            box.blockSignals(False)

    def _change_hotkey(self, combo: HotKeyCombo) -> None:
        result = self.app.apply_hotkey(combo)
        if result.ok:
            self.recorder.set_combo(combo)
            self.hotkey_msg.setText(f"Shortcut set to {combo.display}.")
        else:
            self.recorder.set_combo(self.s.hotkey)
            self.hotkey_msg.setText(result.message)

    def _history(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        form = QFormLayout()
        form.addRow("Keep history for", self._combo(
            [("1 week", 7), ("1 month", 30), ("3 months", 90), ("1 year", 365), ("Forever", 0)],
            self.s.retention_days, lambda x: self._set("retention_days", x)))
        form.addRow("Keep at most", self._combo(
            [("1,000 items", 1000), ("5,000 items", 5000), ("10,000 items", 10000), ("50,000 items", 50000),
             ("Unlimited", 0)], self.s.max_entries, lambda x: self._set("max_entries", x)))
        form.addRow("Largest image to save", self._combo(
            [("5 MB", 5), ("25 MB", 25), ("100 MB", 100)], self.s.max_image_mb, lambda x: self._set("max_image_mb", x)))
        v.addLayout(form)
        v.addWidget(self._note("Pinned and favourite items are never removed by automatic cleanup or by Clear."))
        v.addSpacing(10)
        self.storage = QLabel()
        v.addWidget(self.storage)
        row = QHBoxLayout()
        clear = QPushButton("Clear history (keeps pinned & favourites)…")
        clear.setProperty("plain", True)
        clear.clicked.connect(self.app.confirm_clear_history)
        openf = QPushButton("Open data folder")
        openf.setProperty("plain", True)
        openf.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.app.data_dir))))
        row.addWidget(clear)
        row.addWidget(openf)
        row.addStretch()
        v.addLayout(row)
        v.addStretch()
        self._refresh_storage()
        return w

    def _refresh_storage(self) -> None:
        st = self.app.store
        self.storage.setText(f"{st.total_count():,} items · {human_size(st.storage_bytes())} stored in {self.app.data_dir}")

    def showEvent(self, ev) -> None:
        self._refresh_storage()
        self._refresh_ocr()
        super().showEvent(ev)

    def _privacy(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        detect = QCheckBox("Detect passwords, API keys and tokens")
        detect.setChecked(self.s.protect_sensitive)
        detect.toggled.connect(lambda on: self._set("protect_sensitive", on))
        v.addWidget(detect)
        row = QHBoxLayout()
        row.addWidget(QLabel("When detected"))
        row.addStretch()
        row.addWidget(self._combo([("Don’t save it", "skip"), ("Save it anyway", "store")],
                                  self.s.sensitive_action, lambda x: self._set("sensitive_action", x)))
        v.addLayout(row)
        v.addWidget(self._note("Detection is a best-effort heuristic: it will miss some secrets and flag some harmless text. "
                               "Copies that password managers mark as private (and Windows’ own “don’t record” formats) "
                               "are always ignored."))
        v.addSpacing(8)
        v.addWidget(QLabel("Ignored apps"))
        self.apps = QListWidget()
        self.apps.setMaximumHeight(110)
        self.apps.addItems(self.s.excluded_apps)
        v.addWidget(self.apps)
        row = QHBoxLayout()
        add = QPushButton("Add by name…")
        add.setProperty("plain", True)
        add.clicked.connect(self._add_app_by_name)
        pick = QPushButton("Choose .exe…")
        pick.setProperty("plain", True)
        pick.clicked.connect(self._pick_app)
        rem = QPushButton("Remove")
        rem.setProperty("plain", True)
        rem.clicked.connect(self._remove_app)
        for b in (add, pick, rem):
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        v.addWidget(self._note("Copies made inside ignored apps are never saved. Use the program’s file name, e.g. keepass.exe."))
        v.addSpacing(8)
        v.addWidget(self._note("About privacy: history is stored only on this PC, in " + str(self.app.data_dir) +
                               ". The app has no network code, no telemetry, and never writes clipboard contents to logs. "
                               "It needs no administrator rights."))
        v.addStretch()
        return w

    def _save_apps(self) -> None:
        self._set("excluded_apps", [self.apps.item(i).text() for i in range(self.apps.count())])

    def _add_app(self, name: str) -> None:
        name = name.strip().lower()
        if name and name not in [self.apps.item(i).text().lower() for i in range(self.apps.count())]:
            self.apps.addItem(name if name.endswith(".exe") else name + ".exe")
            self._save_apps()

    def _add_app_by_name(self) -> None:
        text, ok = QInputDialog.getText(self, "Ignore an app", "Program file name (e.g. keepass.exe):")
        if ok:
            self._add_app(text)

    def _pick_app(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a program", "", "Programs (*.exe)")
        if path:
            self._add_app(os.path.basename(path))

    def _remove_app(self) -> None:
        for item in self.apps.selectedItems():
            self.apps.takeItem(self.apps.row(item))
        self._save_apps()

    def _images(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        ocr = QCheckBox("Read text inside copied images (OCR, on this PC)")
        ocr.setChecked(self.s.ocr_enabled)
        ocr.toggled.connect(self._toggle_ocr)
        v.addWidget(ocr)
        self.ocr_status = self._note("")
        v.addWidget(self.ocr_status)
        row = QHBoxLayout()
        retry = QPushButton("Retry failed images")
        retry.setProperty("plain", True)
        retry.clicked.connect(self._retry_ocr)
        row.addWidget(retry)
        row.addStretch()
        v.addLayout(row)
        v.addWidget(self._note("Text found in an image is stored next to it so the image can be found by searching for words "
                               "it contains. The original image is never changed. Recognition runs in the background using the "
                               "bundled Tesseract engine and works fully offline."))
        v.addStretch()
        return w

    def _toggle_ocr(self, on: bool) -> None:
        self._set("ocr_enabled", on)
        self._refresh_ocr()

    def _retry_ocr(self) -> None:
        n = self.app.ocr.retry_failed()
        self.ocr_status.setText(f"Retrying {n} image{'s' if n != 1 else ''}…" if n else "No failed images.")

    def _refresh_ocr(self) -> None:
        if not hasattr(self, "ocr_status"):
            return
        reason = self.app.ocr.unavailable_reason()
        if reason:
            self.ocr_status.setText("OCR engine unavailable: " + reason + " Everything else keeps working.")
        else:
            self.ocr_status.setText("Engine: Tesseract (bundled), language: English. " +
                                    ("" if self.s.ocr_enabled else "OCR is turned off."))
