"""Platform abstraction. The Windows implementation lives in windows.py; dev.py is for development off-Windows."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core.models import ClipPayload

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8


@dataclass(frozen=True)
class HotKeyCombo:
    vk: int                 # Windows virtual-key code
    modifiers: int          # MOD_* flags
    display: str

    @staticmethod
    def default() -> "HotKeyCombo":
        return HotKeyCombo(vk=0x56, modifiers=MOD_CONTROL | MOD_SHIFT, display="Ctrl+Shift+V")


@dataclass
class HotKeyResult:
    ok: bool
    message: str = ""


@dataclass
class ClipboardSnapshot:
    """Everything the capture pipeline needs from one clipboard state."""
    excluded: bool = False                    # a password manager / the OS asked not to record this
    ours: bool = False                        # written by Clipboard Memory itself
    text: Optional[str] = None
    rtf: Optional[bytes] = None
    html: Optional[bytes] = None
    image_png: Optional[bytes] = None
    image_width: int = 0
    image_height: int = 0
    file_paths: list[str] = field(default_factory=list)
    source_app: Optional[str] = None          # friendly name, e.g. "Google Chrome"
    source_id: Optional[str] = None           # stable id used for the exclusion list, e.g. "chrome.exe"


class PlatformBackend(ABC):
    name = "base"

    @abstractmethod
    def start(self, on_clipboard_change: Callable[[], None], on_hotkey: Callable[[], None]) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def register_hotkey(self, combo: HotKeyCombo) -> HotKeyResult: ...

    @abstractmethod
    def read_clipboard(self, include_own: bool = False) -> Optional[ClipboardSnapshot]: ...

    @abstractmethod
    def write_clipboard(self, payload: ClipPayload, plain: bool = False) -> bool: ...

    @abstractmethod
    def write_text(self, text: str) -> bool: ...

    def bring_to_front(self, hwnd: int) -> None:
        """Best-effort: give our window keyboard focus even though another app is in the foreground."""

    def autostart_enabled(self) -> bool:
        return False

    def set_autostart(self, enabled: bool) -> Optional[str]:
        """Returns an error message, or None on success."""
        return "Launch at startup isn't supported on this platform."
