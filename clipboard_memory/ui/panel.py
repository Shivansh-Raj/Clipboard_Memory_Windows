"""The floating history window (layout and interactions follow V1's HistoryView/PanelController)."""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable, Optional

from PySide6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QObject, QPoint, QPropertyAnimation, QRect,
                            QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QColor, QCursor, QFontMetrics, QGuiApplication, QImage, QKeyEvent, QPainter, QPainterPath,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QListView, QMenu, QPlainTextEdit,
                               QPushButton, QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate, QToolButton,
                               QVBoxLayout, QWidget)

from ..core import classifier, transforms
from ..core.models import (MONOSPACE_KINDS, OCR_DONE, OCR_FAILED, OCR_PENDING, ClipEntry, ContentKind,
                           HistoryFilter)
from ..platform.base import PlatformBackend
from . import icons
from .controller import HistoryController
from .theme import Palette, Theme, mono_font, stylesheet, ui_font

MARGIN = 14          # transparent ring around the card; doubles as the resize border
ROW_H = 54


# ------------------------------------------------------------------ formatting

def relative_time(ts: float) -> str:
    d = time.time() - ts
    if d < 45:
        return "just now"
    if d < 3600:
        return f"{max(1, int(d // 60))} min ago"
    if d < 86400:
        return f"{int(d // 3600)} hr ago"
    if d < 172800:
        return "yesterday"
    if d < 7 * 86400:
        return f"{int(d // 86400)} days ago"
    return time.strftime("%b %d", time.localtime(ts)).replace(" 0", " ")


def absolute_time(ts: float) -> str:
    t = time.localtime(ts)
    return time.strftime("%b %d, %Y, ", t).replace(" 0", " ") + time.strftime("%I:%M %p", t).lstrip("0")


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def one_line(text: str, limit: int = 160) -> str:
    return " ".join(text[:limit * 3].split())[:limit]


def css_color(value: str) -> QColor:
    """Parse '#rrggbb' or 'rgba(r,g,b,a)' (QSS style) into a QColor."""
    if value.startswith("rgba"):
        r, g, b, a = [float(x) for x in value[5:-1].split(",")]
        return QColor(int(r), int(g), int(b), int(a * 255))
    return QColor(value)


# ------------------------------------------------------------------ thumbnails

class _Job(QRunnable):
    def __init__(self, cache: "ThumbnailCache", path: str, size: int):
        super().__init__()
        self.cache, self.path, self.size = cache, path, size

    def run(self) -> None:
        img = QImage(self.path)
        if not img.isNull():
            s = self.size * 2
            mode = Qt.KeepAspectRatio if self.size > 100 else Qt.KeepAspectRatioByExpanding
            img = img.scaled(s, s, mode, Qt.SmoothTransformation)
        self.cache.loaded.emit(self.path, self.size, img)


class ThumbnailCache(QObject):
    loaded = Signal(str, int, QImage)
    updated = Signal()

    def __init__(self):
        super().__init__()
        self._cache: "OrderedDict[tuple[str, int], QPixmap]" = OrderedDict()
        self._pending: set[tuple[str, int]] = set()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)
        self.loaded.connect(self._on_loaded)

    def get(self, path: str, size: int) -> Optional[QPixmap]:
        key = (path, size)
        pm = self._cache.get(key)
        if pm is not None:
            self._cache.move_to_end(key)
            return pm
        if key not in self._pending:
            self._pending.add(key)
            self._pool.start(_Job(self, path, size))
        return None

    def _on_loaded(self, path: str, size: int, img: QImage) -> None:
        self._pending.discard((path, size))
        if img.isNull():
            return
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(2.0)
        self._cache[(path, size)] = pm
        while len(self._cache) > 300:
            self._cache.popitem(last=False)
        self.updated.emit()


# ------------------------------------------------------------------ list

class ClipListModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.entries: list[ClipEntry] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.entries)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.UserRole and 0 <= index.row() < len(self.entries):
            return self.entries[index.row()]
        return None

    def set_entries(self, entries: list[ClipEntry]) -> None:
        self.beginResetModel()
        self.entries = list(entries)
        self.endResetModel()


class ClipDelegate(QStyledItemDelegate):
    def __init__(self, panel: "HistoryPanel"):
        super().__init__(panel)
        self.panel = panel

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW_H)

    def paint(self, painter: QPainter, option, index) -> None:
        e: ClipEntry = index.data(Qt.UserRole)
        if e is None:
            return
        pal = self.panel.palette_()
        selected = e.id == self.panel.controller.selected_id
        hover = bool(option.state & QStyle.State_MouseOver)
        dpr = icons.screen_dpr()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(6, 2, -6, -2)
        if selected or hover:
            painter.setBrush(QColor(pal.accent) if selected else css_color(pal.hover))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 8, 8)

        tint = QColor(e.kind.tint)
        tile = QRectF(rect.left() + 8, rect.top() + (rect.height() - 34) / 2, 34, 34)
        thumb = None
        if e.kind == ContentKind.IMAGE and e.image_file:
            thumb = self.panel.thumbs.get(str(self.panel.controller.store.images_dir / e.image_file), 34)
        if thumb is not None:
            path = QPainterPath()
            path.addRoundedRect(tile, 8, 8)
            painter.setClipPath(path)
            sz = thumb.deviceIndependentSize()
            painter.drawPixmap(QRectF(tile.center().x() - sz.width() / 2, tile.center().y() - sz.height() / 2,
                                      sz.width(), sz.height()).toRect(), thumb)
            painter.setClipping(False)
        else:
            painter.setBrush(QColor(255, 255, 255, 50) if selected else QColor(tint.red(), tint.green(), tint.blue(), 40))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(tile, 8, 8)
            glyph = icons.pixmap(e.kind.icon, "#FFFFFF" if selected else e.kind.tint, 16, dpr)
            painter.drawPixmap(int(tile.center().x() - 8), int(tile.center().y() - 8), glyph)

        left = tile.right() + 10
        right = rect.right() - 10 - (14 * (int(e.pinned) + int(e.favorite)))
        text_w = int(right - left)
        fg = QColor("#FFFFFF") if selected else QColor(pal.text)
        sub = QColor(255, 255, 255, 205) if selected else css_color(pal.text2)

        font = mono_font(12.5) if e.kind in (ContentKind.CODE, ContentKind.JSON) else ui_font(13)
        painter.setFont(font)
        painter.setPen(fg)
        title = (f"Image  {e.image_width}×{e.image_height}" if e.kind == ContentKind.IMAGE else one_line(e.text))
        if not title:
            title = e.kind.label
        painter.drawText(QRect(int(left), int(rect.top() + 7), text_w, 18), Qt.AlignVCenter | Qt.AlignLeft,
                         QFontMetrics(font).elidedText(title, Qt.ElideRight, text_w))

        meta = relative_time(e.last_copied_at)
        if e.source_app:
            meta += f" · {e.source_app}"
        if e.kind == ContentKind.IMAGE and e.ocr_status == OCR_PENDING:
            meta += " · reading text…"
        elif e.tags:
            meta += " · " + ", ".join(e.tags[:2])
        mfont = ui_font(10.5)
        painter.setFont(mfont)
        painter.setPen(sub)
        painter.drawText(QRect(int(left), int(rect.top() + 27), text_w, 16), Qt.AlignVCenter | Qt.AlignLeft,
                         QFontMetrics(mfont).elidedText(meta, Qt.ElideRight, text_w))

        x = rect.right() - 14
        for flag, name, col in ((e.favorite, "star", "#FFD60A"), (e.pinned, "pin", "#FF9F0A")):
            if flag:
                painter.drawPixmap(int(x - 11), int(rect.top() + 9), icons.pixmap(name, "#FFFFFF" if selected else col, 11, dpr))
                x -= 14
        painter.restore()


# ------------------------------------------------------------------ preview

class PreviewPane(QWidget):
    def __init__(self, panel: "HistoryPanel"):
        super().__init__()
        self.panel = panel
        self.c = panel.controller
        self._tag_entry_id: Optional[int] = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.empty = QLabel("Select an item to preview")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)

        self.body = QWidget()
        b = QVBoxLayout(self.body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(0)

        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.kind_icon = QLabel()
        self.kind_label = QLabel()
        self.domain = QLabel()
        self.copied = QLabel()
        self.copied.setObjectName("faint")
        row.addWidget(self.kind_icon)
        row.addWidget(self.kind_label)
        row.addWidget(self.domain)
        row.addStretch()
        row.addWidget(self.copied)
        hl.addLayout(row)
        self.meta = QLabel()
        self.meta.setObjectName("muted")
        hl.addWidget(self.meta)
        b.addWidget(head)
        b.addWidget(self._divider())

        self.stack = QStackedWidget()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFrameShape(QFrame.NoFrame)
        self.text.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.text.setViewportMargins(6, 6, 6, 6)
        self.stack.addWidget(self.text)

        img_page = QWidget()
        il = QVBoxLayout(img_page)
        il.setContentsMargins(10, 10, 10, 6)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        il.addWidget(self.image, 1)
        self.ocr_status = QLabel()
        self.ocr_status.setObjectName("muted")
        self.ocr_status.setWordWrap(True)
        il.addWidget(self.ocr_status)
        self.ocr_box = QPlainTextEdit()
        self.ocr_box.setReadOnly(True)
        self.ocr_box.setMaximumHeight(78)
        self.ocr_box.setFont(ui_font(11.5))
        il.addWidget(self.ocr_box)
        self.stack.addWidget(img_page)
        b.addWidget(self.stack, 1)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setContentsMargins(14, 0, 14, 6)
        self.note.setWordWrap(True)
        b.addWidget(self.note)
        b.addWidget(self._divider())

        tags = QHBoxLayout()
        tags.setContentsMargins(14, 6, 14, 6)
        self.tag_icon = QLabel()
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("Add tags, separated by commas")
        self.tag_edit.setFont(ui_font(12))
        self.tag_edit.installEventFilter(self)
        self.tag_edit.returnPressed.connect(self._commit_tags)
        tags.addWidget(self.tag_icon)
        tags.addWidget(self.tag_edit, 1)
        b.addLayout(tags)

        actions = QHBoxLayout()
        actions.setContentsMargins(12, 2, 12, 10)
        actions.setSpacing(6)
        self.copy_btn = QPushButton(" Copy")
        self.copy_btn.setObjectName("primary")
        self.copy_btn.setFocusPolicy(Qt.NoFocus)
        self.copy_btn.clicked.connect(lambda: self.c.copy_selected())
        self.pin_btn = self._tool_button(lambda: self.c.toggle_pin())
        self.fav_btn = self._tool_button(lambda: self.c.toggle_favorite())
        self.del_btn = self._tool_button(lambda: self.c.delete_selected())
        for w in (self.copy_btn, self.pin_btn, self.fav_btn, self.del_btn):
            actions.addWidget(w)
        actions.addStretch()
        self.ctx_layout = QHBoxLayout()
        self.ctx_layout.setSpacing(8)
        actions.addLayout(self.ctx_layout)
        b.addLayout(actions)

        v.addWidget(self.empty)
        v.addWidget(self.body)
        self.body.hide()

    @staticmethod
    def _divider() -> QFrame:
        f = QFrame()
        f.setProperty("divider", True)
        f.setFixedHeight(1)
        return f

    def _tool_button(self, slot: Callable) -> QPushButton:
        btn = QPushButton()
        btn.setProperty("tool", True)
        btn.setFixedWidth(32)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(slot)
        return btn

    def _ctx_button(self, text: str, icon_name: str, slot: Callable) -> QPushButton:
        btn = QPushButton(" " + text)
        btn.setProperty("tool", True)
        btn.setIcon(icons.icon(icon_name, self.panel.palette_().text, 14))
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(slot)
        btn.setMinimumWidth(btn.sizeHint().width())
        self.ctx_layout.addWidget(btn)
        return btn

    def eventFilter(self, obj, ev):
        if obj is self.tag_edit:
            if ev.type() == QEvent.FocusIn:
                self.c.editing_tags = True
            elif ev.type() == QEvent.FocusOut:
                self.c.editing_tags = False
                self._commit_tags(refocus=False)
        return False

    def _commit_tags(self, refocus: bool = True) -> None:
        e = self.c.selected
        if e and e.id == self._tag_entry_id and self.tag_edit.text().strip() != ", ".join(e.tags):
            self.c.set_tags(e, self.tag_edit.text())
        if refocus and self.tag_edit.hasFocus():
            self.tag_edit.clearFocus()
            self.c.focusSearchRequested.emit()

    def refresh(self) -> None:
        pal = self.panel.palette_()
        e = self.c.selected
        if e is None:
            self.empty.show()
            self.body.hide()
            return
        self.empty.hide()
        self.body.show()
        dpr = icons.screen_dpr()

        self.kind_icon.setPixmap(icons.pixmap(e.kind.icon, e.kind.tint, 14, dpr))
        self.kind_label.setText(e.kind.label)
        self.kind_label.setStyleSheet(f"color: {e.kind.tint}; font-weight: 600; font-size: 11.5px;")
        dom = classifier.domain(e.text) if e.kind == ContentKind.LINK else None
        self.domain.setVisible(bool(dom))
        if dom:
            self.domain.setText(dom)
            self.domain.setStyleSheet("color: #0A84FF; background: rgba(10,132,255,0.15); border-radius: 8px; "
                                      "padding: 1px 7px; font-size: 11.5px; font-weight: 500;")
        self.copied.setText(f"copied {e.copy_count}×" if e.copy_count > 1 else "")
        meta = absolute_time(e.last_copied_at)
        if e.source_app:
            meta += f" · from {e.source_app}"
        meta += " · " + (f"{e.image_width}×{e.image_height} · {human_size(e.byte_size)}" if e.kind == ContentKind.IMAGE
                         else f"{e.text_length:,} characters")
        self.meta.setText(meta)

        if e.kind == ContentKind.IMAGE and e.image_file:
            self.stack.setCurrentIndex(1)
            self._show_image(e)
            self.note.setText("")
        else:
            self.stack.setCurrentIndex(0)
            self.text.setFont(mono_font(12) if e.kind in MONOSPACE_KINDS else ui_font(13))
            if self.text.toPlainText() != self.c.preview_text:
                self.text.setPlainText(self.c.preview_text)
            self.note.setText(f"Preview shows the first 20,000 of {e.text_length:,} characters. Copying restores everything."
                              if e.text_length > 20_000 else "")
        self.note.setVisible(bool(self.note.text()))

        if self._tag_entry_id != e.id:
            self._tag_entry_id = e.id
            self.tag_edit.setText(", ".join(e.tags))
        self.tag_icon.setPixmap(icons.pixmap("tag", pal.text2, 13, dpr))

        self.copy_btn.setIcon(icons.icon("copy", "#FFFFFF", 14))
        self.pin_btn.setIcon(icons.icon("pin", "#FF9F0A" if e.pinned else pal.text, 14))
        self.pin_btn.setToolTip(("Unpin" if e.pinned else "Pin") + " (Ctrl+P)")
        self.fav_btn.setIcon(icons.icon("star", "#FFD60A" if e.favorite else pal.text, 14))
        self.fav_btn.setToolTip("Favourite (Ctrl+D)")
        self.del_btn.setIcon(icons.icon("trash", pal.text, 14))
        self.del_btn.setToolTip("Delete (Del)")
        self._build_context_actions(e)

    def _show_image(self, e: ClipEntry) -> None:
        path = str(self.c.store.images_dir / e.image_file)
        pm = self.panel.thumbs.get(path, 900)
        if pm is not None and self.image.width() > 10:
            ratio = pm.devicePixelRatio()
            scaled = pm.scaled(self.image.size() * ratio, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            self.image.setPixmap(scaled)
        elif pm is None:
            self.image.setPixmap(QPixmap())
            self.image.setText("Loading…")
        status = {OCR_PENDING: "Reading text…", OCR_FAILED: "Text extraction failed.",
                  OCR_DONE: "" if e.ocr_text else "No text found in this image."}.get(e.ocr_status, "")
        if e.ocr_status == OCR_FAILED and self.c.ocr and self.c.ocr.last_error:
            status = f"Text extraction failed: {self.c.ocr.last_error}"
        self.ocr_status.setText(status)
        self.ocr_status.setVisible(bool(status))
        self.ocr_box.setPlainText(e.ocr_text or "")
        self.ocr_box.setVisible(bool(e.ocr_text))

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        e = self.c.selected
        if self.body.isVisible() and self.stack.currentIndex() == 1 and e and e.image_file:
            self._show_image(e)

    def _build_context_actions(self, e: ClipEntry) -> None:
        while self.ctx_layout.count():
            w = self.ctx_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        if e.kind == ContentKind.IMAGE:
            if e.ocr_status == OCR_FAILED:
                self._ctx_button("Retry OCR", "refresh", self.c.retry_ocr)
            elif e.ocr_status == OCR_DONE and e.ocr_text:
                self._ctx_button("Copy Text", "scan", self.c.extract_text)
            elif e.ocr_status != OCR_PENDING:
                self._ctx_button("Extract Text", "scan", self.c.extract_text)
            self._ctx_button("Save…", "download", self.c.save_image)
        elif e.kind == ContentKind.FILE:
            return
        else:
            if e.kind == ContentKind.LINK:
                self._ctx_button("Open", "open", self.c.open_link)
            self._add_transform_menu(e)

    def _add_transform_menu(self, e: ClipEntry) -> None:
        btn = QToolButton()
        btn.setProperty("tool", True)
        btn.setText(" Transform")
        btn.setIcon(icons.icon("wand", self.panel.palette_().text, 14))
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setFocusPolicy(Qt.NoFocus)
        menu = QMenu(btn)
        c = self.c
        if e.kind == ContentKind.JSON:
            menu.addAction("Format JSON", lambda: c.transform("Format JSON", transforms.format_json))
            menu.addAction("Minify JSON", lambda: c.transform("Minify JSON", transforms.minify_json))
            menu.addSeparator()
        menu.addAction("Format SQL", lambda: c.transform("Format SQL", transforms.format_sql))
        menu.addAction("UPPERCASE", lambda: c.transform("Uppercase", transforms.uppercased))
        menu.addAction("lowercase", lambda: c.transform("Lowercase", transforms.lowercased))
        menu.addAction("Clean whitespace", lambda: c.transform("Clean whitespace", transforms.clean_whitespace))
        menu.addSeparator()
        menu.addAction("Copy without formatting", lambda: c.copy_selected(plain=True))
        btn.setMenu(menu)
        btn.setMinimumWidth(btn.sizeHint().width() + 4)
        self.ctx_layout.addWidget(btn)


# ------------------------------------------------------------------ panel

class HistoryPanel(QWidget):
    openSettingsRequested = Signal()
    hidden = Signal()

    def __init__(self, controller: HistoryController, theme: Theme, backend: PlatformBackend, settings):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.controller, self.theme, self.backend, self.settings = controller, theme, backend, settings
        self.thumbs = ThumbnailCache()
        self._modal_depth = 0
        self._was_active = False
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(740 + 2 * MARGIN, 420 + 2 * MARGIN)
        self.setWindowTitle("Clipboard Memory")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.card = QFrame()
        self.card.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        v = QVBoxLayout(self.card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        srow = QHBoxLayout()
        srow.setContentsMargins(16, 12, 16, 8)
        self.search_icon = QLabel()
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search clipboard history")
        self.search.setFont(ui_font(17))
        self.search.textChanged.connect(controller.set_search)
        srow.addWidget(self.search_icon)
        srow.addSpacing(6)
        srow.addWidget(self.search, 1)
        v.addLayout(srow)

        frow = QHBoxLayout()
        frow.setContentsMargins(16, 0, 16, 10)
        frow.setSpacing(6)
        self.chips: dict[HistoryFilter, QPushButton] = {}
        for f in HistoryFilter:
            b = QPushButton(f.title)
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setFixedHeight(24)          # QSS drops rounding if the radius exceeds half the height
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, flt=f: controller.set_filter(flt))
            frow.addWidget(b)
            self.chips[f] = b
        frow.addStretch()
        v.addLayout(frow)
        v.addWidget(self._divider())

        body = QHBoxLayout()
        body.setSpacing(0)
        self.model = ClipListModel()
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setItemDelegate(ClipDelegate(self))
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.setStyleSheet("QListView { background: transparent; }")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.clicked.connect(lambda idx: controller.select(idx.data(Qt.UserRole).id))
        self.list.doubleClicked.connect(lambda idx: (controller.select(idx.data(Qt.UserRole).id),
                                                     controller.copy_selected(close=True)))
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.list.verticalScrollBar().valueChanged.connect(self._maybe_load_more)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.empty.setWordWrap(True)
        self.empty.setObjectName("muted")
        self.empty.setContentsMargins(20, 70, 20, 0)
        left = QVBoxLayout()
        left.setContentsMargins(0, 4, 0, 4)
        left.addWidget(self.list, 1)
        left.addWidget(self.empty, 1)
        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setFixedWidth(310)
        body.addWidget(left_w)
        vd = QFrame()
        vd.setProperty("vdivider", True)
        body.addWidget(vd)
        self.preview = PreviewPane(self)
        body.addWidget(self.preview, 1)
        v.addLayout(body, 1)
        v.addWidget(self._divider())

        frow2 = QHBoxLayout()
        frow2.setContentsMargins(14, 6, 14, 7)
        self.hints = QLabel("↵ Copy    Ctrl+↵ Copy & close    Alt+↵ Plain text    Ctrl+P Pin    Del Delete    Esc Close")
        self.hints.setObjectName("faint")
        self.count = QLabel()
        self.count.setObjectName("faint")
        self.clear_btn = QPushButton(" Clear")
        self.clear_btn.setProperty("tool", True)
        self.clear_btn.setFocusPolicy(Qt.NoFocus)
        self.clear_btn.setToolTip("Clear history (pinned items are always kept)")
        self.clear_btn.clicked.connect(self._clear_clicked)
        self._clear_armed = False
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self._disarm_clear)
        frow2.addWidget(self.hints)
        frow2.addStretch()
        frow2.addWidget(self.count)
        frow2.addSpacing(8)
        frow2.addWidget(self.clear_btn)
        v.addLayout(frow2)

        self.toast = QLabel(self.card)
        self.toast.setObjectName("toast")
        self.toast.hide()
        self._toast_fx = QGraphicsOpacityEffect(self.toast)
        self.toast.setGraphicsEffect(self._toast_fx)
        self._toast_anim = QPropertyAnimation(self._toast_fx, b"opacity", self)
        self._toast_anim.finished.connect(self._toast_done)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._fade_toast)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(110)

        c = controller
        c.entriesChanged.connect(self._on_entries)
        c.selectionChanged.connect(self._on_selection)
        c.toastRequested.connect(self.show_toast)
        c.focusSearchRequested.connect(self.focus_search)
        c.closeRequested.connect(self.hide_panel)
        c.scrollToTopRequested.connect(lambda: self.list.scrollToTop())
        c.searchTextRequested.connect(self._set_search_text)
        c.modalBegin.connect(lambda: self._modal(+1))
        c.modalEnd.connect(lambda: self._modal(-1))
        self.thumbs.updated.connect(self._thumb_ready)
        theme.changed.connect(self.apply_theme)
        self.apply_theme()

    def palette_(self) -> Palette:
        return self.theme.palette

    def apply_theme(self) -> None:
        pal = self.palette_()
        self.setStyleSheet(stylesheet(pal))
        self.search_icon.setPixmap(icons.pixmap("search", pal.text2, 17, icons.screen_dpr()))
        self.clear_btn.setIcon(icons.icon("trash", pal.text2, 13))
        self._on_entries()
        self.preview.refresh()

    @staticmethod
    def _divider() -> QFrame:
        f = QFrame()
        f.setProperty("divider", True)
        f.setFixedHeight(1)
        return f

    # ------------------------------------------------------------------ data -> view

    def _on_entries(self) -> None:
        c = self.controller
        self.model.set_entries(c.entries)
        for f, b in self.chips.items():
            b.setChecked(f == c.filter)
        empty = not c.entries
        self.list.setVisible(not empty)
        self.empty.setVisible(empty)
        if empty:
            self.empty.setText("No matches" if c.search else
                               "Nothing here yet\n\nCopy something with Ctrl+C and it will appear here.")
        self.count.setText(f"{c.total:,} item{'s' if c.total != 1 else ''}")
        self._scroll_to_selected()

    def _on_selection(self) -> None:
        self.list.viewport().update()
        self.preview.refresh()
        self._scroll_to_selected()

    def _scroll_to_selected(self) -> None:
        sid = self.controller.selected_id
        for i, e in enumerate(self.model.entries):
            if e.id == sid:
                self.list.scrollTo(self.model.index(i), QAbstractItemView.EnsureVisible)
                break

    def _thumb_ready(self) -> None:
        self.list.viewport().update()
        sel = self.controller.selected
        if self.preview.body.isVisible() and sel and sel.kind == ContentKind.IMAGE:
            self.preview.refresh()

    def _maybe_load_more(self, value: int) -> None:
        if value >= self.list.verticalScrollBar().maximum() - ROW_H * 6:
            self.controller.load_more_if_needed()

    def _set_search_text(self, text: str) -> None:
        if self.search.text() != text:
            self.search.blockSignals(True)
            self.search.setText(text)
            self.search.blockSignals(False)

    def _list_menu(self, pos: QPoint) -> None:
        idx = self.list.indexAt(pos)
        if not idx.isValid():
            return
        e: ClipEntry = idx.data(Qt.UserRole)
        self.controller.select(e.id)
        m = QMenu(self)
        m.addAction("Copy", lambda: self.controller.copy_selected())
        m.addAction("Copy as plain text", lambda: self.controller.copy_selected(plain=True))
        m.addSeparator()
        m.addAction("Unpin" if e.pinned else "Pin", lambda: self.controller.toggle_pin())
        m.addAction("Remove from favourites" if e.favorite else "Add to favourites", lambda: self.controller.toggle_favorite())
        m.addSeparator()
        m.addAction("Delete", lambda: self.controller.delete_selected())
        m.exec(self.list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ clear

    def _clear_clicked(self) -> None:
        """Two-step, no modal dialog (a dialog would steal focus and close the panel)."""
        if not self._clear_armed:
            self._clear_armed = True
            self.clear_btn.setText(" Click again to clear")
            self._clear_timer.start(3000)
            return
        self._disarm_clear()
        self.controller.clear_unpinned()

    def _disarm_clear(self) -> None:
        self._clear_armed = False
        self.clear_btn.setText(" Clear")

    # ------------------------------------------------------------------ toast

    def show_toast(self, message: str) -> None:
        self._toast_anim.stop()
        self.toast.setText(message)
        self.toast.adjustSize()
        self.toast.move((self.card.width() - self.toast.width()) // 2, self.card.height() - 62)
        self._toast_fx.setOpacity(1.0)
        self.toast.show()
        self.toast.raise_()
        self._toast_timer.start(1400)

    def _fade_toast(self) -> None:
        self._toast_anim.setDuration(200)
        self._toast_anim.setStartValue(1.0)
        self._toast_anim.setEndValue(0.0)
        self._toast_anim.start()

    def _toast_done(self) -> None:
        if self._toast_fx.opacity() < 0.05:
            self.toast.hide()

    # ------------------------------------------------------------------ show / hide

    def focus_search(self) -> None:
        self.search.setFocus(Qt.OtherFocusReason)
        self.search.selectAll()

    def toggle(self) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.hide_panel()
        else:
            self.show_panel()

    def show_panel(self) -> None:
        self.controller.prepare_for_show()
        self._was_active = False
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w = min(max(self.settings.panel_width, 740) + 2 * MARGIN, avail.width() - 20)
        h = min(max(self.settings.panel_height, 420) + 2 * MARGIN, avail.height() - 20)
        self.resize(w, h)
        self.move(avail.center().x() - w // 2, avail.top() + int(avail.height() * 0.16) - MARGIN // 2)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.backend.bring_to_front(int(self.winId()))
        self.focus_search()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        QApplication.instance().installEventFilter(self)

    def hide_panel(self) -> None:
        if not self.isVisible():
            return
        self._disarm_clear()
        QApplication.instance().removeEventFilter(self)
        self.settings.panel_width = self.width() - 2 * MARGIN
        self.settings.panel_height = self.height() - 2 * MARGIN
        self.hide()
        self.hidden.emit()

    def _modal(self, delta: int) -> None:
        self._modal_depth = max(0, self._modal_depth + delta)
        if delta < 0 and self.isVisible():
            self.activateWindow()
            self.focus_search()

    def event(self, ev) -> bool:
        if ev.type() == QEvent.WindowActivate:
            self._was_active = True
        elif ev.type() == QEvent.WindowDeactivate and self._was_active:
            QTimer.singleShot(120, self._maybe_hide_after_deactivate)
        return super().event(ev)

    def _maybe_hide_after_deactivate(self) -> None:
        if not self.isVisible() or self._modal_depth > 0 or self.isActiveWindow():
            return
        active = QApplication.activeWindow()
        if active is not None and active is not self:      # one of our own dialogs/menus has focus
            return
        self.hide_panel()

    # ------------------------------------------------------------------ drag / resize

    def _edges_at(self, pos: QPoint) -> Qt.Edges:
        e = Qt.Edges()
        if pos.x() < MARGIN:
            e |= Qt.LeftEdge
        if pos.x() > self.width() - MARGIN:
            e |= Qt.RightEdge
        if pos.y() < MARGIN:
            e |= Qt.TopEdge
        if pos.y() > self.height() - MARGIN:
            e |= Qt.BottomEdge
        return e

    def mouseMoveEvent(self, ev) -> None:
        edges = self._edges_at(ev.position().toPoint())
        if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeBDiagCursor)
        elif edges & (Qt.LeftEdge | Qt.RightEdge):
            self.setCursor(Qt.SizeHorCursor)
        elif edges & (Qt.TopEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self.windowHandle():
            edges = self._edges_at(ev.position().toPoint())
            if edges:
                self.windowHandle().startSystemResize(edges)
            else:
                self.windowHandle().startSystemMove()      # drag from any non-interactive area
            return
        super().mousePressEvent(ev)

    # ------------------------------------------------------------------ keyboard

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.KeyPress and isinstance(obj, QWidget) and (obj is self or self.isAncestorOf(obj)):
            if self.handle_key(ev):
                return True
        return False

    def handle_key(self, ev: QKeyEvent) -> bool:
        c = self.controller
        mods = ev.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier)
        ctrl, shift, alt, none = (mods == Qt.ControlModifier, mods == Qt.ShiftModifier,
                                  mods == Qt.AltModifier, mods == Qt.NoModifier)
        key = ev.key()
        editing = c.editing_tags

        if key == Qt.Key_Escape:
            if editing:
                self.preview.tag_edit.clearFocus()
                c.editing_tags = False
                self.focus_search()
            else:
                self.hide_panel()
            return True
        if ctrl and key == Qt.Key_F:
            self.focus_search()
            return True
        if ctrl and key == Qt.Key_P:
            c.toggle_pin()
            return True
        if ctrl and key == Qt.Key_D:
            c.toggle_favorite()
            return True
        if ctrl and key == Qt.Key_W:
            self.hide_panel()
            return True
        if ctrl and key == Qt.Key_Comma:
            self.openSettingsRequested.emit()
            return True
        if editing:
            return False
        if none and key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown):
            c.move({Qt.Key_Up: -1, Qt.Key_Down: 1, Qt.Key_PageUp: -6, Qt.Key_PageDown: 6}[key])
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if ctrl:
                c.copy_selected(close=True)
            elif alt:
                c.copy_selected(plain=True)
            elif none:
                c.copy_selected()
            else:
                return False
            return True
        if key == Qt.Key_Delete:
            if ctrl or (none and not c.search):
                c.delete_selected()
                return True
            return False
        if key in (Qt.Key_Tab, Qt.Key_Backtab) and (none or shift):
            c.cycle_filter(-1 if (shift or key == Qt.Key_Backtab) else 1)
            return True
        if ctrl and Qt.Key_1 <= key <= Qt.Key_8:
            c.set_filter(list(HistoryFilter)[key - Qt.Key_1])
            return True
        return False
