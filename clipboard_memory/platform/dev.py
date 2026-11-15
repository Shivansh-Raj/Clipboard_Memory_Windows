"""Development backend for running the UI off-Windows (macOS/Linux) using Qt's clipboard. Not shipped."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData
from PySide6.QtGui import QGuiApplication, QImage

from ..core.models import ClipPayload, ContentKind
from . import images
from .base import ClipboardSnapshot, HotKeyCombo, HotKeyResult, PlatformBackend


class DevBackend(PlatformBackend):
    name = "dev"

    def __init__(self):
        self._own_write = False
        self._on_change: Callable[[], None] = lambda: None
        self.autostart = False

    def start(self, on_clipboard_change, on_hotkey) -> None:
        self._on_change = on_clipboard_change
        QGuiApplication.clipboard().dataChanged.connect(self._changed)

    def _changed(self) -> None:
        if self._own_write:
            self._own_write = False
            return
        self._on_change()

    def stop(self) -> None: ...

    def register_hotkey(self, combo: HotKeyCombo) -> HotKeyResult:
        return HotKeyResult(False, "Global shortcuts are only available on Windows. (Development backend.)")

    def read_clipboard(self, include_own: bool = False) -> Optional[ClipboardSnapshot]:
        md = QGuiApplication.clipboard().mimeData()
        if md is None:
            return None
        snap = ClipboardSnapshot(source_app="Dev", source_id="dev")
        if md.hasUrls() and all(u.isLocalFile() for u in md.urls()):
            snap.file_paths = [u.toLocalFile() for u in md.urls()]
        if md.hasText():
            snap.text = md.text().replace("\r\n", "\n")
        if md.hasFormat("text/rtf"):
            snap.rtf = bytes(md.data("text/rtf"))
        if md.hasHtml():
            snap.html = md.html().encode("utf-8")
        if not snap.text and md.hasImage():
            img = md.imageData()
            if isinstance(img, QImage) and not img.isNull():
                png = images.image_to_png(img)
                if png:
                    snap.image_png, snap.image_width, snap.image_height = png, img.width(), img.height()
        return snap

    def write_clipboard(self, payload: ClipPayload, plain: bool = False) -> bool:
        md = QMimeData()
        if payload.kind == ContentKind.IMAGE and payload.image_path:
            img = QImage(payload.image_path)
            if img.isNull():
                return False
            md.setImageData(img)
        else:
            md.setText(payload.text or "")
            if not plain and payload.rtf:
                md.setData("text/rtf", QByteArray(payload.rtf))
            if not plain and payload.html:
                md.setHtml(payload.html.decode("utf-8", "replace"))
        self._own_write = True
        QGuiApplication.clipboard().setMimeData(md)
        return True

    def write_text(self, text: str) -> bool:
        self._own_write = True
        QGuiApplication.clipboard().setText(text)
        return True

    def autostart_enabled(self) -> bool:
        return self.autostart

    def set_autostart(self, enabled: bool) -> Optional[str]:
        self.autostart = enabled
        return None
