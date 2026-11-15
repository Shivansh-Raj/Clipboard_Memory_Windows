"""View-model for the history panel (mirrors V1's HistoryViewModel)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from ..core import classifier, transforms
from ..core.models import ClipEntry, ContentKind, HistoryFilter, NewClip, OCR_DONE
from ..core.store import HistoryStore
from ..ocr.service import OcrService
from ..services import ClipboardWriter

log = logging.getLogger("clipboard_memory.ui")
PAGE = 100


class HistoryController(QObject):
    resultsReady = Signal(int, object, int, bool)       # generation, entries, total, select_first
    moreReady = Signal(int, object)
    previewReady = Signal(int, str)
    entriesChanged = Signal()
    selectionChanged = Signal()
    toastRequested = Signal(str)
    focusSearchRequested = Signal()
    closeRequested = Signal()
    scrollToTopRequested = Signal()
    historyChanged = Signal()                           # thread-safe "something was saved/updated"
    searchTextRequested = Signal(str)
    modalBegin = Signal()
    modalEnd = Signal()

    def __init__(self, store: HistoryStore, writer: ClipboardWriter, ocr: Optional[OcrService] = None):
        super().__init__()
        self.store, self.writer, self.ocr = store, writer, ocr
        self.search = ""
        self.filter = HistoryFilter.ALL
        self.entries: list[ClipEntry] = []
        self.total = 0
        self.selected_id: Optional[int] = None
        self.preview_text = ""
        self.editing_tags = False
        self._gen = 0
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-query")
        self.resultsReady.connect(self._on_results)
        self.moreReady.connect(self._on_more)
        self.previewReady.connect(self._on_preview)
        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(80)
        self._coalesce.timeout.connect(lambda: self.reload(reset_scroll=False))
        self.historyChanged.connect(self._coalesce.start)

    # ------------------------------------------------------------------ state

    @property
    def selected(self) -> Optional[ClipEntry]:
        return next((e for e in self.entries if e.id == self.selected_id), None)

    def prepare_for_show(self) -> None:
        self.search, self.filter, self.editing_tags = "", HistoryFilter.ALL, False
        self.searchTextRequested.emit("")
        self.reload(select_first=True)
        self.scrollToTopRequested.emit()
        self.focusSearchRequested.emit()

    def set_search(self, text: str) -> None:
        if text != self.search:
            self.search = text
            self.reload()

    def set_filter(self, flt: HistoryFilter) -> None:
        if flt != self.filter:
            self.filter = flt
            self.reload()
        else:
            self.entriesChanged.emit()

    def cycle_filter(self, delta: int) -> None:
        allf = list(HistoryFilter)
        self.set_filter(allf[(allf.index(self.filter) + delta) % len(allf)])

    # ------------------------------------------------------------------ loading

    def reload(self, select_first: bool = False, reset_scroll: bool = True) -> None:
        self._gen += 1
        gen, q, f = self._gen, self.search, self.filter
        limit = PAGE if (reset_scroll or len(self.entries) <= PAGE) else len(self.entries)

        def job():
            try:
                rows = self.store.fetch(q, f, limit)
                total = self.store.count(q, f)
                self.resultsReady.emit(gen, rows, total, select_first)
            except Exception:
                log.exception("history query failed")
        self._pool.submit(job)

    def _on_results(self, gen: int, rows: list, total: int, select_first: bool) -> None:
        if gen != self._gen:
            return
        self.entries, self.total = rows, total
        if select_first or not any(e.id == self.selected_id for e in rows):
            self.selected_id = rows[0].id if rows else None
        self.entriesChanged.emit()
        self.refresh_preview()
        self.selectionChanged.emit()

    def load_more_if_needed(self) -> None:
        if len(self.entries) >= self.total:
            return
        offset, gen, q, f = len(self.entries), self._gen, self.search, self.filter
        self._pool.submit(lambda: self.moreReady.emit(gen, self.store.fetch(q, f, PAGE, offset)))

    def _on_more(self, gen: int, more: list) -> None:
        if gen != self._gen:
            return
        known = {e.id for e in self.entries}
        self.entries.extend(e for e in more if e.id not in known)
        self.entriesChanged.emit()

    def refresh_preview(self) -> None:
        e = self.selected
        if e is None:
            self.preview_text = ""
            return
        if e.kind == ContentKind.IMAGE:
            self.preview_text = e.ocr_text or ""
            return
        self.preview_text = e.text
        if e.is_truncated:
            eid = e.id
            self._pool.submit(lambda: self.previewReady.emit(eid, (self.store.full_text(eid) or "")[:20_000]))

    def _on_preview(self, eid: int, text: str) -> None:
        if self.selected_id == eid:
            self.preview_text = text
            self.selectionChanged.emit()

    # ------------------------------------------------------------------ navigation

    def move(self, delta: int) -> None:
        if not self.entries:
            return
        idx = next((i for i, e in enumerate(self.entries) if e.id == self.selected_id),
                   -1 if delta > 0 else len(self.entries))
        nxt = min(max(idx + delta, 0), len(self.entries) - 1)
        self.select(self.entries[nxt].id)
        if nxt >= len(self.entries) - 5:
            self.load_more_if_needed()

    def select(self, entry_id: int) -> None:
        self.selected_id = entry_id
        self.refresh_preview()
        self.selectionChanged.emit()

    # ------------------------------------------------------------------ actions

    def toast(self, message: str) -> None:
        self.toastRequested.emit(message)

    def copy_selected(self, plain: bool = False, close: bool = False) -> None:
        e = self.selected
        if not e:
            return
        if self.writer.restore(e.id, plain=plain):
            self.toast("Copied as plain text" if plain else "Copied to clipboard")
            self.reload(reset_scroll=False)
            if close:
                self.closeRequested.emit()
        else:
            self.toast("Couldn’t restore this item")

    def toggle_pin(self, e: Optional[ClipEntry] = None) -> None:
        e = e or self.selected
        if e:
            self.store.set_pinned(e.id, not e.pinned)
            self.toast("Unpinned" if e.pinned else "Pinned")
            self.reload(reset_scroll=False)

    def toggle_favorite(self, e: Optional[ClipEntry] = None) -> None:
        e = e or self.selected
        if e:
            self.store.set_favorite(e.id, not e.favorite)
            self.toast("Removed from favourites" if e.favorite else "Added to favourites")
            self.reload(reset_scroll=False)

    def delete_selected(self, e: Optional[ClipEntry] = None) -> None:
        e = e or self.selected
        if not e:
            return
        idx = next((i for i, x in enumerate(self.entries) if x.id == e.id), None)
        self.store.delete([e.id])
        if idx is not None and e.id == self.selected_id:
            rest = [x for x in self.entries if x.id != e.id]
            self.selected_id = rest[min(idx, len(rest) - 1)].id if rest else None
        self.toast("Deleted")
        self.reload(reset_scroll=False)

    def clear_unpinned(self) -> int:
        """Clear the history. Pinned entries are always kept. Returns how many were removed."""
        before = self.store.total_count()
        self.store.clear(keep_pinned=True)
        removed = before - self.store.total_count()
        self.reload(select_first=True)
        kept = self.store.total_count()
        self.toast(f"Cleared {removed:,} item{'s' if removed != 1 else ''}" + (f" · {kept} pinned kept" if kept else ""))
        return removed

    def set_tags(self, e: ClipEntry, text: str) -> None:
        self.store.set_tags(e.id, text.split(","))
        self.reload(reset_scroll=False)

    def transform(self, name: str, fn: Callable[[str], Optional[str]]) -> None:
        """Runs a transformation, stores the result as its own entry and puts it on the clipboard."""
        e = self.selected
        full = self.store.full_text(e.id) if e else None
        if full is None:
            return
        result = fn(full)
        if result is None:
            self.toast(f"{name} isn’t possible for this content")
            return
        if result == full:
            self.toast("Nothing to change")
            return
        self.writer.write_text(result)
        new_id, _ = self.store.save(NewClip(kind=classifier.classify(result), text=result,
                                            source_app="Clipboard Memory"))
        self.selected_id = new_id
        self.search, self.filter = "", HistoryFilter.ALL
        self.searchTextRequested.emit("")
        self.reload()
        self.scrollToTopRequested.emit()
        self.toast(f"{name} – copied as new entry")

    def open_link(self) -> None:
        e = self.selected
        if not e:
            return
        s = e.text.strip()
        if s.lower().startswith("www."):
            s = "https://" + s
        url = QUrl(s)
        if url.scheme().lower() not in ("http", "https", "mailto", "ftp"):
            self.toast("Not a web link")
            return
        QDesktopServices.openUrl(url)
        self.closeRequested.emit()

    def save_image(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        e = self.selected
        if not e or not e.image_file:
            return
        src = self.store.images_dir / e.image_file
        self.modalBegin.emit()
        dest, _ = QFileDialog.getSaveFileName(None, "Save image", "Clipboard Image.png", "PNG image (*.png)")
        self.modalEnd.emit()
        if not dest:
            return
        try:
            with open(src, "rb") as fi, open(dest, "wb") as fo:
                fo.write(fi.read())
            self.toast("Image saved")
        except OSError:
            self.toast("Couldn’t save the image")

    # ---- OCR

    def extract_text(self) -> None:
        """Image action: copy the recognised text as a new entry, or (re)run OCR when there is none yet."""
        e = self.selected
        if not e or e.kind != ContentKind.IMAGE or not self.ocr:
            return
        if e.ocr_status == OCR_DONE and e.ocr_text:
            text = e.ocr_text
            self.writer.write_text(text)
            self.store.save(NewClip(kind=classifier.classify(text), text=text, source_app="Clipboard Memory"))
            self.toast("Text copied to clipboard")
            self.reload(reset_scroll=False)
            return
        self.retry_ocr()

    def retry_ocr(self) -> None:
        e = self.selected
        if not e or not self.ocr:
            return
        reason = self.ocr.unavailable_reason()
        if reason:
            self.toast(reason)
            return
        self.ocr.submit(e.id, force=True)
        self.toast("Reading text…")
        self.reload(reset_scroll=False)
