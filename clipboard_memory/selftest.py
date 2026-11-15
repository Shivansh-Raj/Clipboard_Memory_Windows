"""Self-tests run against the *packaged* app in CI (and by hand): `ClipboardMemory.exe --selftest-ocr out.json`.

The exe is windowed, so results go to a JSON file and the exit code (0 = pass).
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path


def _write(out: str | None, result: dict) -> int:
    if out:
        Path(out).write_text(json.dumps(result, indent=2), "utf-8")
    else:
        print(json.dumps(result, indent=2))
    return 0 if result.get("passed") else 1


def _wait(cond, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def selftest_ocr(out: str | None) -> int:
    """Copy-an-image flow without a UI: store the image, let the bundled Tesseract read it in the background,
    then find the original image by searching for words that are only visible inside it."""
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
    from .core.models import ContentKind, NewClip
    from .core.store import HistoryStore
    from .ocr.preprocess import prepare
    from .ocr.service import OcrService
    from .ocr.tesseract import TesseractEngine
    from .platform import images

    result: dict = {"passed": False, "steps": {}}
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    engine = TesseractEngine()
    result["engine"] = {"exe": str(engine.exe), "tessdata": str(engine.tessdata),
                        "unavailable_reason": engine.unavailable_reason()}
    if engine.unavailable_reason():
        return _write(out, result)

    img = QImage(900, 260, QImage.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setPen(QColor("black"))
    font = QFont("Arial")
    font.setPixelSize(54)
    p.setFont(font)
    p.drawText(30, 100, "Clipboard Memory OCR test")
    p.drawText(30, 190, "Invoice 4821 total")
    p.end()
    png = images.image_to_png(img)

    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(Path(tmp) / "data")
        svc = OcrService(store, engine, Path(tmp) / "work", preprocess=prepare)
        svc.start()
        eid, is_new = store.save(NewClip(kind=ContentKind.IMAGE, image_png=png, image_width=900, image_height=260,
                                         ocr_pending=True))
        result["steps"]["image_stored"] = bool(is_new)
        svc.submit(eid)
        done = _wait(lambda: store.entry(eid).ocr_status in ("done", "failed"), 120)
        entry = store.entry(eid)
        text = (entry.ocr_text or "") if entry else ""
        result["steps"]["ocr_finished"] = done and entry.ocr_status == "done"
        result["steps"]["ocr_text_length"] = len(text)       # content itself is deliberately not recorded
        low = text.lower()
        result["steps"]["text_recognised"] = "clipboard" in low and "4821" in low
        found = store.fetch(search="invoice 4821")
        result["steps"]["search_returns_original_image"] = bool(found) and found[0].id == eid
        orig = store.payload(eid).image_path
        result["steps"]["original_image_intact"] = Path(orig).read_bytes() == png
        # Diagnostics (synthetic text only): what each path actually read, to make failures explainable.
        raw = Path(tmp) / "raw.png"
        raw.write_bytes(png)
        dbg: dict = {"service_text": text}
        try:
            dbg["raw_text"] = engine.recognize(raw)
            prepared = prepare(raw, Path(tmp) / "work2")
            dbg["prepared_text"] = engine.recognize(prepared) if prepared else None
        except Exception as e:                      # diagnostics must never change the verdict
            dbg["error"] = repr(e)
        result["debug"] = dbg
        svc.stop()
        store.close()
    result["passed"] = all(v is True for k, v in result["steps"].items() if k != "ocr_text_length")
    return _write(out, result)


def selftest_windows(out: str | None) -> int:
    """Exercises the real Windows clipboard and hotkey layer."""
    if sys.platform != "win32":
        return _write(out, {"passed": False, "error": "Windows only"})
    from PySide6.QtGui import QColor, QGuiApplication, QImage
    from .core.models import ClipPayload, ContentKind
    from .platform import images
    from .platform.base import MOD_ALT, MOD_CONTROL, MOD_SHIFT, HotKeyCombo
    from .platform.windows import WindowsBackend, user32

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    be = WindowsBackend()
    changes, hotkeys = [], []
    be.start(lambda: changes.append(1), lambda: hotkeys.append(1))
    steps: dict = {}
    info: dict = {}
    try:
        text = "def f(x):\r\n    return x * 2  # naïve ✓\r\n"
        steps["write_text"] = be.write_text(text)
        steps["clipboard_update_event"] = _wait(lambda: len(changes) > 0, 3)
        own = be.read_clipboard()
        steps["own_write_detected"] = bool(own and own.ours)
        snap = be.read_clipboard(include_own=True)
        steps["text_round_trip"] = bool(snap and snap.text == text.replace("\r\n", "\n"))

        rtf = rb"{\rtf1\ansi Hello \b bold\b0}"
        steps["write_rtf"] = be.write_clipboard(ClipPayload(kind=ContentKind.RICH_TEXT, text="Hello bold", rtf=rtf))
        snap = be.read_clipboard(include_own=True)
        steps["rtf_round_trip"] = bool(snap and snap.rtf == rtf)
        be.write_clipboard(ClipPayload(kind=ContentKind.RICH_TEXT, text="Hello bold", rtf=rtf), plain=True)
        snap = be.read_clipboard(include_own=True)
        steps["plain_drops_formatting"] = bool(snap and snap.rtf is None and snap.text == "Hello bold")

        img = QImage(64, 32, QImage.Format_ARGB32)
        img.fill(QColor("#336699"))
        png = images.image_to_png(img)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.png"
            path.write_bytes(png)
            steps["write_image"] = be.write_clipboard(ClipPayload(kind=ContentKind.IMAGE, image_path=str(path)))
            snap = be.read_clipboard(include_own=True)
            steps["image_round_trip"] = bool(snap and snap.image_png and (snap.image_width, snap.image_height) == (64, 32))

            steps["write_files"] = be.write_clipboard(ClipPayload(kind=ContentKind.FILE, text=str(path),
                                                                  file_paths=[str(path)]))
            snap = be.read_clipboard(include_own=True)
            steps["files_round_trip"] = bool(snap and snap.file_paths == [str(path)])

        combo = HotKeyCombo(0x7A, MOD_CONTROL | MOD_ALT | MOD_SHIFT, "Ctrl+Alt+Shift+F11")   # F11
        res = be.register_hotkey(combo)
        steps["hotkey_registered"] = res.ok
        info["hotkey_message"] = res.message
        if res.ok:
            # Press the combination for real; informational because CI sessions may not deliver synthetic input.
            for vk in (0x11, 0x12, 0x10, 0x7A):
                user32.keybd_event(vk, 0, 0, 0)
            for vk in (0x7A, 0x10, 0x12, 0x11):
                user32.keybd_event(vk, 0, 2, 0)
            info["hotkey_fired_by_synthetic_input"] = _wait(lambda: len(hotkeys) > 0, 2)
        clash = be.register_hotkey(combo)
        steps["re_register_same_combo"] = clash.ok
    finally:
        be.stop()
    info["autostart_api"] = "winreg"
    result = {"passed": all(v is True for v in steps.values()), "steps": steps, "info": info}
    return _write(out, result)


def run(argv: list[str]) -> int:
    flag = argv[0]
    out = argv[1] if len(argv) > 1 else None
    if flag == "--selftest-ocr":
        return selftest_ocr(out)
    if flag == "--selftest-windows":
        return selftest_windows(out)
    return _write(out, {"passed": False, "error": f"unknown self-test {flag}"})
