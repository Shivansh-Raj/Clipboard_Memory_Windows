"""OCR engine interface. Implement this to swap Tesseract for something else."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class OcrError(Exception):
    """Raised when text could not be extracted (engine missing, crashed, timed out...)."""


class OcrEngine(ABC):
    name = "ocr"

    @abstractmethod
    def unavailable_reason(self) -> str | None:
        """None when the engine is ready to use, otherwise a short human-readable reason."""

    @abstractmethod
    def recognize(self, image_path: Path) -> str:
        """Return the text found in the image ('' when there is none). Raise OcrError on failure."""
