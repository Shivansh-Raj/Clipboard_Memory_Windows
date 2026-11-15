"""Capture pipeline (clipboard -> history) and restore (history -> clipboard). Mirrors V1's monitor/writer."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

from .core import classifier, sensitive
from .core.models import ClipPayload, ContentKind, NewClip
from .core.store import HistoryStore
from .ocr.service import OcrService
from .platform.base import ClipboardSnapshot, PlatformBackend
from .settings import Settings

log = logging.getLogger("clipboard_memory.capture")

MAX_TEXT_BYTES = 4_000_000
MAX_RICH_BYTES = 5_000_000


class ClipboardMonitor:
    """Receives 'clipboard changed' events from any thread and ingests them serially on a worker thread.

    Clipboard content is never logged.
    """

    def __init__(self, store: HistoryStore, backend: PlatformBackend, settings: Settings,
                 ocr: Optional[OcrService] = None, on_saved: Callable[[], None] = lambda: None):
        self.store, self.backend, self.settings, self.ocr, self.on_saved = store, backend, settings, ocr, on_saved
        self._q: "queue.Queue[Optional[bool]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._q.put(None)

    def notify_changed(self) -> None:
        if self._q.qsize() < 8:                    # bursts collapse; dedupe handles repeats
            self._q.put(True)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                if not self.settings.paused:
                    snap = self.backend.read_clipboard()
                    if snap is not None:
                        self.ingest(snap)
            except Exception:
                log.exception("capture failed")

    def ingest(self, snap: ClipboardSnapshot) -> Optional[tuple[int, bool]]:
        s = self.settings
        if snap.ours or snap.excluded:
            return None
        if snap.source_id and snap.source_id.lower() in {a.lower() for a in s.excluded_apps}:
            return None

        clip: Optional[NewClip] = None
        if snap.file_paths:
            clip = NewClip(kind=ContentKind.FILE, text="\n".join(snap.file_paths))
        elif snap.text is not None and snap.text.strip():
            if len(snap.text.encode("utf-8", "ignore")) > MAX_TEXT_BYTES:
                return None
            if s.protect_sensitive and s.sensitive_action == "skip" and sensitive.detect(snap.text):
                return None                          # nothing about the content is logged
            kind = classifier.classify(snap.text)
            if kind == ContentKind.TEXT and (snap.rtf or snap.html):
                kind = ContentKind.RICH_TEXT
            clip = NewClip(kind=kind, text=snap.text,
                           rtf=snap.rtf if snap.rtf and len(snap.rtf) < MAX_RICH_BYTES else None,
                           html=snap.html if snap.html and len(snap.html) < MAX_RICH_BYTES else None)
        elif snap.image_png:
            if len(snap.image_png) > s.max_image_mb * 1_048_576 or snap.image_width <= 0:
                return None
            clip = NewClip(kind=ContentKind.IMAGE, image_png=snap.image_png, image_width=snap.image_width,
                           image_height=snap.image_height, ocr_pending=bool(s.ocr_enabled and self.ocr))
        if clip is None:
            return None
        clip.source_app, clip.source_id = snap.source_app, snap.source_id
        result = self.store.save(clip)
        if result[1] and clip.kind == ContentKind.IMAGE and self.ocr and s.ocr_enabled:
            self.ocr.submit(result[0])               # background; capture has already finished
        self.on_saved()
        return result


class ClipboardWriter:
    """Writes stored entries back to the system clipboard."""

    def __init__(self, store: HistoryStore, backend: PlatformBackend):
        self.store, self.backend = store, backend

    def restore(self, entry_id: int, plain: bool = False) -> bool:
        payload = self.store.payload(entry_id)
        if payload is None:
            return False
        if not self.backend.write_clipboard(payload, plain=plain):
            return False
        self.store.touch(entry_id)
        return True

    def write_text(self, text: str) -> bool:
        return self.backend.write_text(text)
