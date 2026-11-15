# Third-party notices

Clipboard Memory V2 bundles the following components. Each keeps its own licence.

| Component | Used for | Licence |
|---|---|---|
| **Tesseract OCR** 5.4.0 (Windows build by UB Mannheim) and its `eng` language data | Offline text recognition in images | Apache-2.0 |
| Leptonica, libpng, libjpeg(-turbo), libtiff, zlib, libwebp, libopenjp2, giflib and other DLLs shipped with the Tesseract build | Image decoding for Tesseract | BSD-2-Clause / libpng / IJG / zlib / BSD-style (see each project) |
| **Qt 6** via **PySide6** (Qt for Python) | User interface | LGPL-3.0 (Qt libraries are bundled as separate DLLs and can be replaced) |
| **Python** runtime | Application runtime | PSF License |
| **PyInstaller** bootloader | Packaging | GPL-2.0 with the bootloader exception (applies to the generated app too) |

The licence texts for Tesseract and for the libraries shipped with it are in `tesseract/licenses/` inside the
application folder, together with `tesseract/SOURCE.txt`, which records the exact download and its SHA-256.
Qt's licence terms are at https://www.qt.io/licensing/ and the PySide6 source at https://code.qt.io/.

Clipboard Memory itself makes no network connections and collects no telemetry.
