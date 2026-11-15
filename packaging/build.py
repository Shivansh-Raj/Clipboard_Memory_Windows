"""Build the Windows app folder + zip with PyInstaller.   python packaging/build.py

Prerequisite: python packaging/fetch_tesseract.py   (stages vendor/tesseract)
Result: dist/ClipboardMemory/ClipboardMemory.exe  and  dist/ClipboardMemory-<version>-win64.zip
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clipboard_memory import __version__  # noqa: E402


def make_icon(dest: Path) -> None:
    """Assemble a multi-size .ico (PNG-compressed entries) from the runtime-drawn app icon."""
    from PySide6.QtGui import QGuiApplication
    from clipboard_memory.platform.images import image_to_png
    from clipboard_memory.ui import icons
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    ic = icons.app_icon()
    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs = [image_to_png(ic.pixmap(s, s).toImage()) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, blob in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    dest.write_bytes(header + entries + b"".join(blobs))


def main() -> int:
    if sys.platform != "win32" and "--allow-non-windows" not in sys.argv:
        sys.exit("PyInstaller builds for the OS it runs on: run this on Windows (the CI workflow does).")
    vendor = ROOT / "vendor" / "tesseract"
    if not (vendor / "tesseract.exe").exists() and "--no-ocr" not in sys.argv:
        sys.exit("vendor/tesseract is missing: run  python packaging/fetch_tesseract.py  first (or pass --no-ocr).")

    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    icon = build / "icon.ico"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    make_icon(icon)

    sep = os.pathsep
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--noconsole",
            "--name", "ClipboardMemory", "--icon", str(icon),
            "--distpath", str(ROOT / "dist"), "--workpath", str(build / "pyi"), "--specpath", str(build),
            "--add-data", f"{ROOT / 'THIRD_PARTY_NOTICES.md'}{sep}.",
            "--exclude-module", "tkinter", "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtQuick",
            "--exclude-module", "PySide6.QtPdf", "--exclude-module", "PySide6.QtWebEngineCore",
            str(ROOT / "packaging" / "entry.py")]
    if vendor.exists() and "--no-ocr" not in sys.argv:
        args[-1:-1] = ["--add-data", f"{vendor}{sep}tesseract"]
    subprocess.run(args, check=True, cwd=str(ROOT))

    out = ROOT / "dist" / "ClipboardMemory"
    shutil.copy2(ROOT / "README.md", out / "README.md")
    if not (out / "_internal").exists() and not (out / "tesseract").exists() and vendor.exists():
        sys.exit("expected bundled resources were not found in the build output")
    zip_base = ROOT / "dist" / f"ClipboardMemory-{__version__}-win64"
    shutil.make_archive(str(zip_base), "zip", ROOT / "dist", "ClipboardMemory")
    print(f"built {out}\nzip   {zip_base}.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
