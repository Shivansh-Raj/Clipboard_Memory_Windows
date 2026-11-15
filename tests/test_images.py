from PySide6.QtGui import QColor, QGuiApplication, QImage

from clipboard_memory.platform import images


def _png(w=20, h=10):
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor("#336699"))
    return images.image_to_png(img)


def test_png_dib_round_trip():
    QGuiApplication.instance() or QGuiApplication([])
    png = _png()
    assert images.png_size(png) == (20, 10)
    dib = images.png_to_dib(png)
    assert dib and dib[:4] == (40).to_bytes(4, "little")          # BITMAPINFOHEADER
    back, w, h = images.dib_to_png(dib)
    assert (w, h) == (20, 10)
    assert images.png_size(back) == (20, 10)
