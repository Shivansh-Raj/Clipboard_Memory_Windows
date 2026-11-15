from clipboard_memory.core.models import ContentKind as K
from clipboard_memory.core.store import HistoryStore
from clipboard_memory.platform.base import ClipboardSnapshot
from clipboard_memory.services import ClipboardMonitor
from clipboard_memory.settings import Settings


def monitor(tmp_path, **kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    return ClipboardMonitor(HistoryStore(tmp_path), backend=None, settings=s), s


def test_text_code_link_classification_and_dedupe(tmp_path):
    m, _ = monitor(tmp_path)
    code = "def f(x):\n    return x * 2\n"
    a = m.ingest(ClipboardSnapshot(text=code, source_app="Code", source_id="code.exe"))
    b = m.ingest(ClipboardSnapshot(text=code))
    assert a[0] == b[0] and a[1] and not b[1]
    m.ingest(ClipboardSnapshot(text="https://github.com/a/b"))
    assert m.store.entry(a[0]).kind == K.CODE
    assert m.store.payload(a[0]).text == code                    # indentation preserved
    assert m.store.fetch(search="code.exe") == [] and len(m.store.fetch(search="Code")) == 1


def test_sensitive_skipped_by_default_but_can_be_stored(tmp_path):
    m, s = monitor(tmp_path)
    assert m.ingest(ClipboardSnapshot(text="AKIAIOSFODNN7EXAMPLE")) is None
    s.sensitive_action = "store"
    assert m.ingest(ClipboardSnapshot(text="AKIAIOSFODNN7EXAMPLE")) is not None
    s.protect_sensitive = False
    s.sensitive_action = "skip"
    assert m.ingest(ClipboardSnapshot(text="password = hunter22hunter")) is not None


def test_excluded_and_ours_and_password_manager_ignored(tmp_path):
    m, _ = monitor(tmp_path, excluded_apps=["KeePass.exe"])
    assert m.ingest(ClipboardSnapshot(text="x y", source_id="keepass.exe")) is None
    assert m.ingest(ClipboardSnapshot(text="x y", ours=True)) is None
    assert m.ingest(ClipboardSnapshot(text="x y", excluded=True)) is None
    assert m.store.count() == 0


def test_image_capture_marks_ocr_pending(tmp_path):
    class FakeOcr:
        ids = []
        def submit(self, i): self.ids.append(i)
    ocr = FakeOcr()
    m, _ = monitor(tmp_path)
    m.ocr = ocr
    r = m.ingest(ClipboardSnapshot(image_png=b"\x89PNGabc", image_width=3, image_height=2))
    assert m.store.entry(r[0]).ocr_status == "pending" and ocr.ids == [r[0]]
    # a repeat of the same image doesn't re-run OCR
    m.ingest(ClipboardSnapshot(image_png=b"\x89PNGabc", image_width=3, image_height=2))
    assert ocr.ids == [r[0]]


def test_oversized_image_skipped(tmp_path):
    m, _ = monitor(tmp_path, max_image_mb=1)
    assert m.ingest(ClipboardSnapshot(image_png=b"0" * 1_100_000, image_width=5, image_height=5)) is None


def test_rich_text_and_files(tmp_path):
    m, _ = monitor(tmp_path)
    r = m.ingest(ClipboardSnapshot(text="Hello world today", rtf=b"{\\rtf1 Hello}"))
    assert m.store.entry(r[0]).kind == K.RICH_TEXT
    f = m.ingest(ClipboardSnapshot(file_paths=["C:\\a.txt", "C:\\b.txt"]))
    assert m.store.entry(f[0]).kind == K.FILE


def test_settings_round_trip(tmp_path):
    from clipboard_memory.platform.base import HotKeyCombo, MOD_ALT, MOD_CONTROL
    p = tmp_path / "settings.json"
    s = Settings.load(p)
    s.retention_days = 30
    s.excluded_apps = ["keepass.exe"]
    s.set_hotkey(HotKeyCombo(0x56, MOD_CONTROL | MOD_ALT, "Ctrl+Alt+V"))
    s.save()
    again = Settings.load(p)
    assert again.retention_days == 30 and again.excluded_apps == ["keepass.exe"]
    assert again.hotkey.display == "Ctrl+Alt+V" and again.hotkey.modifiers == MOD_CONTROL | MOD_ALT
    p.write_text("{not json")
    assert Settings.load(p).retention_days == 90            # corrupt file falls back to defaults
