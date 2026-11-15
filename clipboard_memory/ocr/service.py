"""Background OCR queue. Never blocks capture or the UI; failures only mark the entry as failed."""
from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Callable, Optional

from ..core.models import OCR_DONE, OCR_FAILED, OCR_PENDING
from ..core.store import HistoryStore
from .base import OcrEngine, OcrError

log = logging.getLogger("clipboard_memory.ocr")


class OcrService:
    def __init__(self, store: HistoryStore, engine: OcrEngine, work_dir: Path,
                 enabled: Callable[[], bool] = lambda: True,
                 on_update: Optional[Callable[[int, str], None]] = None,
                 preprocess: Optional[Callable[[Path, Path], Optional[Path]]] = None):
        self.store, self.engine, self.work_dir = store, engine, Path(work_dir)
        self._enabled = enabled
        self._on_update = on_update or (lambda _id, _status: None)
        self._preprocess = preprocess
        self._queue: "queue.Queue[Optional[int]]" = queue.Queue()
        self._queued: set[int] = set()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="ocr-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)

    def available(self) -> bool:
        return self.engine.unavailable_reason() is None

    def unavailable_reason(self) -> Optional[str]:
        return self.engine.unavailable_reason()

    def submit(self, entry_id: int, force: bool = False) -> None:
        """Queue an entry. Honours the settings toggle unless the user asked explicitly (force)."""
        if not force and not self._enabled():
            return
        with self._lock:
            if entry_id in self._queued:
                return
            self._queued.add(entry_id)
        self.store.set_ocr_status(entry_id, OCR_PENDING)
        self._queue.put(entry_id)

    def requeue_pending(self) -> None:
        """Pick up entries left 'pending' by a restart or crash."""
        if not self._enabled():
            return
        for eid in self.store.pending_ocr_ids():
            self.submit(eid)

    def retry_failed(self) -> int:
        ids = self.store.failed_ocr_ids()
        for eid in ids:
            self.submit(eid, force=True)
        return len(ids)

    # ------------------------------------------------------------------ worker

    def _run(self) -> None:
        while True:
            entry_id = self._queue.get()
            if entry_id is None:
                return
            try:
                self._process(entry_id)
            except Exception:                         # the worker must never die
                log.exception("OCR worker error for entry %s", entry_id)
                self.store.set_ocr_status(entry_id, OCR_FAILED)
                self._on_update(entry_id, OCR_FAILED)
            finally:
                with self._lock:
                    self._queued.discard(entry_id)

    def _process(self, entry_id: int) -> None:
        payload = self.store.payload(entry_id)
        if not payload or not payload.image_path or not Path(payload.image_path).is_file():
            return
        reason = self.engine.unavailable_reason()
        if reason:
            self.last_error = reason
            self.store.set_ocr_status(entry_id, OCR_FAILED)
            self._on_update(entry_id, OCR_FAILED)
            return
        source = Path(payload.image_path)
        processed: Optional[Path] = None
        try:
            if self._preprocess:
                try:
                    processed = self._preprocess(source, self.work_dir)
                except Exception:
                    log.warning("OCR preprocessing failed; using the original image")
            text = self.engine.recognize(processed or source)
            self.store.set_ocr(entry_id, text, OCR_DONE)
            self.last_error = None
            self._on_update(entry_id, OCR_DONE)
        except OcrError as e:
            self.last_error = str(e)
            log.warning("OCR failed for entry %s: %s", entry_id, e)
            self.store.set_ocr_status(entry_id, OCR_FAILED)
            self._on_update(entry_id, OCR_FAILED)
        finally:
            if processed:
                processed.unlink(missing_ok=True)
