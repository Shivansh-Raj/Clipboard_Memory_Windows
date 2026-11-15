"""PNG <-> Windows DIB conversion using Qt (no Pillow needed)."""
from __future__ import annotations

import struct
from typing import Optional

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage


def png_size(png: bytes) -> tuple[int, int]:
    img = QImage.fromData(png, "PNG")
    return (img.width(), img.height()) if not img.isNull() else (0, 0)


def image_to_png(img: QImage) -> Optional[bytes]:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    ok = img.save(buf, "PNG")
    buf.close()
    return bytes(ba) if ok else None


def dib_to_image(dib: bytes) -> Optional[QImage]:
    """Wrap a packed DIB (BITMAPINFOHEADER + pixels) in a BMP file header so Qt can decode it."""
    if len(dib) < 40:
        return None
    size, = struct.unpack_from("<I", dib, 0)
    if size < 40 or size > len(dib):
        return None
    bit_count, = struct.unpack_from("<H", dib, 14)
    compression, = struct.unpack_from("<I", dib, 16)
    clr_used, = struct.unpack_from("<I", dib, 32)
    palette = (clr_used or ((1 << bit_count) if bit_count <= 8 else 0)) * 4
    masks = 12 if (compression == 3 and size == 40) else 0
    offset = 14 + size + masks + palette
    header = b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, offset)
    img = QImage.fromData(header + dib, "BMP")
    return None if img.isNull() else img


def dib_to_png(dib: bytes) -> Optional[tuple[bytes, int, int]]:
    img = dib_to_image(dib)
    if img is None:
        return None
    png = image_to_png(img)
    return (png, img.width(), img.height()) if png else None


def png_to_dib(png: bytes) -> Optional[bytes]:
    img = QImage.fromData(png, "PNG")
    if img.isNull():
        return None
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    ok = img.convertToFormat(QImage.Format_RGB32).save(buf, "BMP")
    buf.close()
    return bytes(ba)[14:] if ok else None
