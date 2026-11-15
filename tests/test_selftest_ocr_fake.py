"""Runs the OCR self-test against a real tesseract when one is available (skipped otherwise)."""
import os
import shutil

import pytest

from clipboard_memory.selftest import selftest_ocr


@pytest.mark.skipif(not (shutil.which("tesseract") or os.environ.get("CLIPBOARD_MEMORY_TESSERACT")),
                    reason="tesseract not installed on this machine; the packaged-exe CI job covers it")
def test_real_tesseract_end_to_end(tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    out = tmp_path / "r.json"
    assert selftest_ocr(str(out)) == 0
