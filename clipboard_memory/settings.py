"""User settings, stored as JSON in the data directory (no registry, no cloud)."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass, field, fields
from pathlib import Path

from .platform.base import HotKeyCombo

log = logging.getLogger("clipboard_memory.settings")


def data_dir() -> Path:
    override = os.environ.get("CLIPBOARD_MEMORY_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Clipboard Memory"
    return Path.home() / ".clipboard-memory"


@dataclass
class Settings:
    hotkey_vk: int = HotKeyCombo.default().vk
    hotkey_modifiers: int = HotKeyCombo.default().modifiers
    hotkey_display: str = HotKeyCombo.default().display
    retention_days: int = 90
    max_entries: int = 10_000
    max_image_mb: int = 25
    excluded_apps: list[str] = field(default_factory=list)       # exe names, e.g. "keepass.exe"
    protect_sensitive: bool = True
    sensitive_action: str = "skip"                              # "skip" | "store"
    ocr_enabled: bool = True
    paused: bool = False
    theme: str = "system"                                       # "system" | "light" | "dark"
    did_show_welcome: bool = False
    panel_width: int = 900
    panel_height: int = 520

    # not persisted
    _path: Path | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    @property
    def hotkey(self) -> HotKeyCombo:
        return HotKeyCombo(self.hotkey_vk, self.hotkey_modifiers, self.hotkey_display)

    def set_hotkey(self, combo: HotKeyCombo) -> None:
        self.hotkey_vk, self.hotkey_modifiers, self.hotkey_display = combo.vk, combo.modifiers, combo.display

    @classmethod
    def load(cls, path: Path) -> "Settings":
        s = cls()
        s._path = path
        try:
            raw = json.loads(path.read_text("utf-8"))
            known = {f.name for f in fields(cls) if not f.name.startswith("_")}
            for k, v in raw.items():
                if k in known and isinstance(v, type(getattr(s, k))):
                    setattr(s, k, v)
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            log.warning("settings file unreadable; using defaults")
        return s

    def save(self) -> None:
        if not self._path:
            return
        with self._lock:
            data = {f.name: getattr(self, f.name) for f in fields(self) if not f.name.startswith("_")}
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2), "utf-8")
                os.replace(tmp, self._path)
            except OSError:
                log.exception("couldn't save settings")
