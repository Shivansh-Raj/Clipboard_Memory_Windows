"""End-to-end through the real app wiring (controller, panel, monitor, OCR service) with the Qt clipboard standing
in for Windows. Verifies the user journey in the definition of done."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent
from PySide6.QtWidgets import QApplication

from clipboard_memory.app import ClipboardMemoryApp
from clipboard_memory.core.models import ContentKind as K
from clipboard_memory.ocr.base import OcrEngine
from clipboard_memory.platform import images
from clipboard_memory.platform.dev import DevBackend


class FakeOcr(OcrEngine):
    def unavailable_reason(self):
        return None

    def recognize(self, path):
        return "Invoice 4821 total due"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def app(qapp, tmp_path):
    a = ClipboardMemoryApp(qapp, backend=DevBackend(), directory=tmp_path, engine=FakeOcr())
    a.settings.did_show_welcome = True
    a.start()
    yield a
    a.shutdown()


def spin(qapp, until=None, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return bool(until and until())


def key(app, k, mods=Qt.NoModifier):
    ev = QKeyEvent(QEvent.KeyPress, k, mods)
    return app.panel.handle_key(ev)


def test_copy_capture_search_restore(qapp, app):
    cb = QGuiApplication.clipboard()
    code = "def add(a, b):\n    return a + b\n"
    cb.setText(code)
    assert spin(qapp, lambda: app.store.count() == 1)
    cb.setText("https://github.com/anthropics/claude-code")
    assert spin(qapp, lambda: app.store.count() == 2)

    app.panel.show_panel()
    assert spin(qapp, lambda: len(app.controller.entries) == 2)
    app.panel.search.setText("github.com")
    assert spin(qapp, lambda: len(app.controller.entries) == 1)
    assert app.controller.selected.kind == K.LINK

    app.panel.search.setText("")
    assert spin(qapp, lambda: len(app.controller.entries) == 2)
    first = app.controller.selected_id
    assert key(app, Qt.Key_Down)                     # keyboard navigation
    assert app.controller.selected_id != first
    cb.setText("something else")                      # our own restore must overwrite this
    spin(qapp, timeout=0.2)
    target = app.controller.selected
    assert key(app, Qt.Key_Return)                    # Enter = copy selected
    assert spin(qapp, lambda: cb.text() == app.store.payload(target.id).text)
    assert key(app, Qt.Key_Return, Qt.ControlModifier)  # Ctrl+Enter = copy and close
    assert spin(qapp, lambda: not app.panel.isVisible())
    # restoring must not create a duplicate of itself (3 = code, link, and the 'something else' text we copied)
    spin(qapp, timeout=0.3)
    assert app.store.count() == 3


def test_pin_tags_delete_keyboard(qapp, app):
    cb = QGuiApplication.clipboard()
    for t in ("alpha entry", "beta entry"):
        cb.setText(t)
        spin(qapp, timeout=0.15)
    assert spin(qapp, lambda: app.store.count() == 2)
    app.panel.show_panel()
    spin(qapp, lambda: len(app.controller.entries) == 2)
    sel = app.controller.selected_id
    assert key(app, Qt.Key_P, Qt.ControlModifier)
    assert spin(qapp, lambda: app.store.entry(sel).pinned)
    assert key(app, Qt.Key_D, Qt.ControlModifier)
    assert spin(qapp, lambda: app.store.entry(sel).favorite)
    app.controller.set_tags(app.controller.selected, "work, sql")
    assert spin(qapp, lambda: app.store.entry(sel).tags == ["work", "sql"])
    assert len(app.store.fetch(search="sql")) == 1
    assert key(app, Qt.Key_Delete)                    # search empty -> deletes selected
    assert spin(qapp, lambda: app.store.count() == 1)
    assert key(app, Qt.Key_Escape)
    assert not app.panel.isVisible()


def test_image_is_ocrd_searched_and_restored(qapp, app):
    cb = QGuiApplication.clipboard()
    img = QImage(80, 40, QImage.Format_RGB32)
    img.fill(QColor("white"))
    cb.setImage(img)
    assert spin(qapp, lambda: app.store.count() == 1)
    entry = app.store.fetch()[0]
    assert entry.kind == K.IMAGE
    assert spin(qapp, lambda: app.store.entry(entry.id).ocr_status == "done")     # background OCR
    app.panel.show_panel()
    app.panel.search.setText("invoice 4821")                                      # search by text inside the image
    assert spin(qapp, lambda: [e.id for e in app.controller.entries] == [entry.id])
    cb.setText("overwritten")
    spin(qapp, timeout=0.2)
    key(app, Qt.Key_Return)
    assert spin(qapp, lambda: not cb.image().isNull())                            # original image restored
    restored = cb.image()
    assert (restored.width(), restored.height()) == (80, 40)
    app.panel.hide_panel()


def test_pause_stops_capture(qapp, app):
    app.set_paused(True)
    QGuiApplication.clipboard().setText("should not be recorded")
    spin(qapp, timeout=0.4)
    assert app.store.count() == 0
    app.set_paused(False)
    QGuiApplication.clipboard().setText("recorded now")
    assert spin(qapp, lambda: app.store.count() == 1)


def test_transform_creates_separate_entry(qapp, app):
    QGuiApplication.clipboard().setText('{"b":1,"a":[1,2]}')
    assert spin(qapp, lambda: app.store.count() == 1)
    app.panel.show_panel()
    spin(qapp, lambda: app.controller.selected is not None)
    original = app.controller.selected.id
    from clipboard_memory.core import transforms
    app.controller.transform("Format JSON", transforms.format_json)
    assert spin(qapp, lambda: app.store.count() == 2)
    assert app.store.payload(original).text == '{"b":1,"a":[1,2]}'                # original untouched
    app.panel.hide_panel()
