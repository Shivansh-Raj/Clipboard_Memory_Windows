import time

from clipboard_memory.core.models import ContentKind as K, NewClip
from clipboard_memory.core.store import HistoryStore
from clipboard_memory.ocr.base import OcrEngine, OcrError
from clipboard_memory.ocr.service import OcrService
from clipboard_memory.ocr.tesseract import TesseractEngine


class FakeEngine(OcrEngine):
    def __init__(self, text="Invoice 4821 total", fail=False, missing=False):
        self.text, self.fail, self.missing = text, fail, missing

    def unavailable_reason(self):
        return "no engine" if self.missing else None

    def recognize(self, image_path):
        if self.fail:
            raise OcrError("boom")
        return self.text


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def make(tmp_path, engine, enabled=True):
    store = HistoryStore(tmp_path / "d")
    events = []
    svc = OcrService(store, engine, tmp_path / "work", enabled=lambda: enabled, on_update=lambda i, s: events.append((i, s)))
    svc.start()
    return store, svc, events


def test_ocr_text_becomes_searchable_and_original_image_untouched(tmp_path):
    store, svc, events = make(tmp_path, FakeEngine())
    png = b"\x89PNGoriginal-bytes"
    eid, _ = store.save(NewClip(kind=K.IMAGE, image_png=png, image_width=4, image_height=4, ocr_pending=True))
    svc.submit(eid)
    assert wait_for(lambda: store.entry(eid).ocr_status == "done")
    assert store.fetch(search="invoice 4821")[0].id == eid
    assert open(store.payload(eid).image_path, "rb").read() == png         # original preserved independently
    assert events[-1] == (eid, "done")


def test_failure_is_recorded_and_retry_succeeds(tmp_path):
    engine = FakeEngine(fail=True)
    store, svc, _ = make(tmp_path, engine)
    eid, _ = store.save(NewClip(kind=K.IMAGE, image_png=b"\x89PNGx", image_width=1, image_height=1, ocr_pending=True))
    svc.submit(eid)
    assert wait_for(lambda: store.entry(eid).ocr_status == "failed")
    assert svc.last_error
    engine.fail = False
    svc.submit(eid, force=True)                                             # retry
    assert wait_for(lambda: store.entry(eid).ocr_status == "done")


def test_missing_engine_does_not_break_anything(tmp_path):
    store, svc, _ = make(tmp_path, FakeEngine(missing=True))
    eid, _ = store.save(NewClip(kind=K.IMAGE, image_png=b"\x89PNGy", image_width=1, image_height=1, ocr_pending=True))
    svc.submit(eid)
    assert wait_for(lambda: store.entry(eid).ocr_status == "failed")
    assert not svc.available()
    store.save(NewClip(kind=K.TEXT, text="still works"))
    assert store.count() == 2


def test_disabled_ocr_skips_unless_forced(tmp_path):
    store, svc, _ = make(tmp_path, FakeEngine(), enabled=False)
    eid, _ = store.save(NewClip(kind=K.IMAGE, image_png=b"\x89PNGz", image_width=1, image_height=1))
    svc.submit(eid)
    time.sleep(0.2)
    assert store.entry(eid).ocr_status == ""
    svc.submit(eid, force=True)
    assert wait_for(lambda: store.entry(eid).ocr_status == "done")


def test_pending_entries_are_requeued_after_restart(tmp_path):
    store, svc, _ = make(tmp_path, FakeEngine())
    eid, _ = store.save(NewClip(kind=K.IMAGE, image_png=b"\x89PNGq", image_width=1, image_height=1, ocr_pending=True))
    svc.requeue_pending()
    assert wait_for(lambda: store.entry(eid).ocr_status == "done")


def test_tesseract_engine_reports_missing_binary(tmp_path):
    e = TesseractEngine(exe=tmp_path / "nope.exe")
    assert e.unavailable_reason()
