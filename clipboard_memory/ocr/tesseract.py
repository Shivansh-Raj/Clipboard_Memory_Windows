"""Tesseract engine. Uses a bundled tesseract.exe located relative to the application, never PATH."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .base import OcrEngine, OcrError

log = logging.getLogger("clipboard_memory.ocr")

EXE_NAME = "tesseract.exe" if os.name == "nt" else "tesseract"
ENV_OVERRIDE = "CLIPBOARD_MEMORY_TESSERACT"      # development / testing only


def app_dir() -> Path:
    """Directory holding the application's bundled resources."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def find_bundled(base: Optional[Path] = None) -> Optional[Path]:
    """Locate tesseract inside the app's own folders: <app>/tesseract/ (packaged) or <repo>/vendor/tesseract/."""
    base = base or app_dir()
    for candidate in (base / "tesseract" / EXE_NAME, base / "vendor" / "tesseract" / EXE_NAME):
        if candidate.is_file():
            return candidate
    return None


class TesseractEngine(OcrEngine):
    name = "Tesseract"

    def __init__(self, exe: Optional[Path] = None, tessdata: Optional[Path] = None, lang: str = "eng",
                 timeout: float = 90.0):
        self.exe = exe or self._discover()
        self.tessdata = tessdata or (self.exe.parent / "tessdata" if self.exe else None)
        self.lang = lang
        self.timeout = timeout

    @staticmethod
    def _discover() -> Optional[Path]:
        override = os.environ.get(ENV_OVERRIDE)
        if override and Path(override).is_file():
            return Path(override)
        bundled = find_bundled()
        if bundled:
            return bundled
        if not getattr(sys, "frozen", False):          # a packaged app never relies on a system install
            found = shutil.which("tesseract")
            if found:
                return Path(found)
        return None

    def unavailable_reason(self) -> Optional[str]:
        if not self.exe or not Path(self.exe).is_file():
            return "The Tesseract engine isn't included in this build."
        if not self.tessdata or not (Path(self.tessdata) / f"{self.lang}.traineddata").is_file():
            return f"Tesseract language data '{self.lang}' is missing."
        return None

    def recognize(self, image_path: Path) -> str:
        reason = self.unavailable_reason()
        if reason:
            raise OcrError(reason)
        cmd = [str(self.exe), str(image_path), "stdout", "--tessdata-dir", str(self.tessdata), "-l", self.lang]
        env = dict(os.environ, OMP_THREAD_LIMIT="1")
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000 | 0x00004000      # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout, env=env,
                                  cwd=str(Path(self.exe).parent), **kwargs)
        except subprocess.TimeoutExpired as e:
            raise OcrError("Text extraction timed out.") from e
        except OSError as e:
            raise OcrError(f"Couldn't start Tesseract ({e.__class__.__name__}).") from e
        if proc.returncode != 0:
            # stderr describes the engine's problem, not the clipboard content, but keep it out of logs anyway.
            raise OcrError(f"Tesseract exited with code {proc.returncode}.")
        return proc.stdout.decode("utf-8", "replace").strip()
