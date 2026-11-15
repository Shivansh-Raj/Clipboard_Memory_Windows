"""Light image clean-up that noticeably improves OCR on screenshots (greyscale + upscale small images)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage


def prepare(src: Path, work_dir: Path) -> Optional[Path]:
    """Return a path to a processed PNG in work_dir, or None to use the original file as is."""
    img = QImage(str(src))
    if img.isNull():
        return None
    w, h = img.width(), img.height()
    scale = 1
    if max(w, h) < 1000:
        scale = 3
    elif max(w, h) < 1800:
        scale = 2
    if scale > 1:
        img = img.scaled(w * scale, h * scale, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if img.hasAlphaChannel():                       # flatten transparency onto white
        flat = QImage(img.size(), QImage.Format_RGB32)
        flat.fill(Qt.white)
        from PySide6.QtGui import QPainter
        p = QPainter(flat)
        p.drawImage(0, 0, img)
        p.end()
        img = flat
    img = img.convertToFormat(QImage.Format_Grayscale8)
    work_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=".png", dir=str(work_dir))
    os.close(fd)
    if not img.save(name, "PNG"):
        Path(name).unlink(missing_ok=True)
        return None
    return Path(name)
