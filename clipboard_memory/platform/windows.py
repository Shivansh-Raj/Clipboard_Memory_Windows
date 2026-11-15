"""Windows backend: clipboard listener, global hotkey, clipboard read/write, autostart. Pure ctypes + winreg.

Everything is user-level: no admin rights, no hooks, no pywin32.
"""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import sys
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes as wt
from pathlib import Path
from typing import Callable, Optional

from ..core.models import ClipPayload, ContentKind
from . import images
from .base import ClipboardSnapshot, HotKeyCombo, HotKeyResult, PlatformBackend

log = logging.getLogger("clipboard_memory.windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
version = ctypes.WinDLL("version", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
VOIDP = ctypes.c_void_p

WM_CLIPBOARDUPDATE, WM_HOTKEY, WM_CLOSE, WM_APP = 0x031D, 0x0312, 0x0010, 0x8000
WM_REGISTER_HOTKEY = WM_APP + 1
HWND_MESSAGE = -3
MOD_NOREPEAT = 0x4000
CF_UNICODETEXT, CF_DIB, CF_HDROP, CF_DIBV5 = 13, 8, 15, 17
GMEM_MOVEABLE = 0x0002
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
HOTKEY_ID = 0xC0DE


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


def _sig(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = list(argtypes)


_sig(user32.RegisterClassW, wt.ATOM, ctypes.POINTER(WNDCLASSW))
_sig(user32.CreateWindowExW, VOIDP, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, ctypes.c_int, VOIDP, VOIDP, VOIDP, VOIDP)
_sig(user32.DefWindowProcW, LRESULT, VOIDP, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.GetMessageW, ctypes.c_int, ctypes.POINTER(wt.MSG), VOIDP, wt.UINT, wt.UINT)
_sig(user32.TranslateMessage, wt.BOOL, ctypes.POINTER(wt.MSG))
_sig(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wt.MSG))
_sig(user32.PostMessageW, wt.BOOL, VOIDP, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.PostQuitMessage, None, ctypes.c_int)
_sig(user32.DestroyWindow, wt.BOOL, VOIDP)
_sig(user32.AddClipboardFormatListener, wt.BOOL, VOIDP)
_sig(user32.RemoveClipboardFormatListener, wt.BOOL, VOIDP)
_sig(user32.RegisterHotKey, wt.BOOL, VOIDP, ctypes.c_int, wt.UINT, wt.UINT)
_sig(user32.UnregisterHotKey, wt.BOOL, VOIDP, ctypes.c_int)
_sig(user32.OpenClipboard, wt.BOOL, VOIDP)
_sig(user32.CloseClipboard, wt.BOOL)
_sig(user32.EmptyClipboard, wt.BOOL)
_sig(user32.GetClipboardData, VOIDP, wt.UINT)
_sig(user32.SetClipboardData, VOIDP, wt.UINT, VOIDP)
_sig(user32.EnumClipboardFormats, wt.UINT, wt.UINT)
_sig(user32.GetClipboardFormatNameW, ctypes.c_int, wt.UINT, wt.LPWSTR, ctypes.c_int)
_sig(user32.RegisterClipboardFormatW, wt.UINT, wt.LPCWSTR)
_sig(user32.GetClipboardOwner, VOIDP)
_sig(user32.GetForegroundWindow, VOIDP)
_sig(user32.GetWindowThreadProcessId, wt.DWORD, VOIDP, ctypes.POINTER(wt.DWORD))
_sig(user32.SetForegroundWindow, wt.BOOL, VOIDP)
_sig(user32.BringWindowToTop, wt.BOOL, VOIDP)
_sig(user32.AttachThreadInput, wt.BOOL, wt.DWORD, wt.DWORD, wt.BOOL)
_sig(kernel32.GlobalAlloc, VOIDP, wt.UINT, ctypes.c_size_t)
_sig(kernel32.GlobalLock, VOIDP, VOIDP)
_sig(kernel32.GlobalUnlock, wt.BOOL, VOIDP)
_sig(kernel32.GlobalSize, ctypes.c_size_t, VOIDP)
_sig(kernel32.GlobalFree, VOIDP, VOIDP)
_sig(kernel32.GetModuleHandleW, VOIDP, wt.LPCWSTR)
_sig(kernel32.GetCurrentThreadId, wt.DWORD)
_sig(kernel32.GetCurrentProcessId, wt.DWORD)
_sig(kernel32.OpenProcess, VOIDP, wt.DWORD, wt.BOOL, wt.DWORD)
_sig(kernel32.CloseHandle, wt.BOOL, VOIDP)
_sig(kernel32.QueryFullProcessImageNameW, wt.BOOL, VOIDP, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
_sig(shell32.DragQueryFileW, wt.UINT, VOIDP, wt.UINT, wt.LPWSTR, wt.UINT)
_sig(version.GetFileVersionInfoSizeW, wt.DWORD, wt.LPCWSTR, ctypes.POINTER(wt.DWORD))
_sig(version.GetFileVersionInfoW, wt.BOOL, wt.LPCWSTR, wt.DWORD, wt.DWORD, VOIDP)
_sig(version.VerQueryValueW, wt.BOOL, VOIDP, wt.LPCWSTR, ctypes.POINTER(VOIDP), ctypes.POINTER(wt.UINT))


class _DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", wt.DWORD), ("ptx", wt.LONG), ("pty", wt.LONG), ("fNC", wt.BOOL), ("fWide", wt.BOOL)]


# ---------------------------------------------------------------------------- helpers

def _fmt(name: str) -> int:
    return user32.RegisterClipboardFormatW(name)


CF_RTF = _fmt("Rich Text Format")
CF_HTML = _fmt("HTML Format")
CF_PNG = _fmt("PNG")
CF_DROPEFFECT = _fmt("Preferred DropEffect")

# Formats that password managers and Windows itself use to say "don't record this".
_EXCLUDE_FORMATS = {"ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore"}
_NO_HISTORY_FORMAT = "CanIncludeInClipboardHistory"


def _normalize_newlines(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


class _ClipboardBusy(Exception):
    pass


@contextmanager
def _clipboard(owner=None, retries: int = 20, delay: float = 0.025):
    """Open the clipboard, retrying: another app may hold it for a few milliseconds."""
    for _ in range(retries):
        if user32.OpenClipboard(owner):
            break
        time.sleep(delay)
    else:
        raise _ClipboardBusy()
    try:
        yield
    finally:
        user32.CloseClipboard()


def _get_bytes(fmt: int) -> Optional[bytes]:
    h = user32.GetClipboardData(fmt)
    if not h:
        return None
    size = kernel32.GlobalSize(h)
    ptr = kernel32.GlobalLock(h)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        kernel32.GlobalUnlock(h)


def _set_bytes(fmt: int, data: bytes) -> bool:
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    ptr = kernel32.GlobalLock(h)
    if not ptr:
        kernel32.GlobalFree(h)
        return False
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(h)
    if not user32.SetClipboardData(fmt, h):
        kernel32.GlobalFree(h)
        return False
    return True


def _format_names() -> dict[int, str]:
    """All formats currently on the clipboard; registered (named) formats map to their names."""
    names: dict[int, str] = {}
    fmt = 0
    buf = ctypes.create_unicode_buffer(256)
    while True:
        fmt = user32.EnumClipboardFormats(fmt)
        if not fmt:
            break
        names[fmt] = buf.value if fmt >= 0xC000 and user32.GetClipboardFormatNameW(fmt, buf, 256) > 0 else ""
    return names


def _file_description(path: str) -> Optional[str]:
    try:
        dummy = wt.DWORD(0)
        size = version.GetFileVersionInfoSizeW(path, ctypes.byref(dummy))
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, data):
            return None
        ptr, length = VOIDP(), wt.UINT()
        if not version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) or length.value < 4:
            return None
        lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
        for key in ("FileDescription", "ProductName"):
            if version.VerQueryValueW(data, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{key}", ctypes.byref(ptr), ctypes.byref(length)) and length.value > 1:
                text = ctypes.wstring_at(ptr.value).strip()
                if text:
                    return text
    except Exception:
        log.debug("version info lookup failed", exc_info=True)
    return None


# ---------------------------------------------------------------------------- backend

class WindowsBackend(PlatformBackend):
    name = "windows"

    def __init__(self):
        self._on_change: Callable[[], None] = lambda: None
        self._on_hotkey: Callable[[], None] = lambda: None
        self._thread: Optional[threading.Thread] = None
        self._hwnd = None
        self._ready = threading.Event()
        self._cmds: "queue.Queue" = queue.Queue()
        self._wndproc = WNDPROC(self._window_proc)         # keep a reference or it is garbage collected
        self._hotkey_active: Optional[HotKeyCombo] = None
        self._name_cache: dict[str, str] = {}

    # -------------------------------------------------------------- message thread

    def start(self, on_clipboard_change, on_hotkey) -> None:
        self._on_change, self._on_hotkey = on_clipboard_change, on_hotkey
        self._thread = threading.Thread(target=self._message_loop, name="win-messages", daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or not self._hwnd:
            raise RuntimeError("Couldn't create the clipboard listener window.")

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)

    def _message_loop(self) -> None:
        cls = WNDCLASSW()
        cls.lpfnWndProc = self._wndproc
        cls.hInstance = kernel32.GetModuleHandleW(None)
        cls.lpszClassName = f"ClipboardMemoryMessages{os.getpid()}"
        user32.RegisterClassW(ctypes.byref(cls))
        self._hwnd = user32.CreateWindowExW(0, cls.lpszClassName, "Clipboard Memory", 0, 0, 0, 0, 0,
                                            HWND_MESSAGE, None, cls.hInstance, None)
        if self._hwnd:
            user32.AddClipboardFormatListener(self._hwnd)
        self._ready.set()
        if not self._hwnd:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _window_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_CLIPBOARDUPDATE:
                self._on_change()
                return 0
            if msg == WM_HOTKEY:
                self._on_hotkey()
                return 0
            if msg == WM_REGISTER_HOTKEY:
                self._handle_register()
                return 0
            if msg == WM_CLOSE:
                user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                user32.RemoveClipboardFormatListener(hwnd)
                user32.DestroyWindow(hwnd)
                return 0
            if msg == 0x0002:                              # WM_DESTROY
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("window procedure error")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # -------------------------------------------------------------- hotkey

    def register_hotkey(self, combo: HotKeyCombo) -> HotKeyResult:
        if not self._hwnd:
            return HotKeyResult(False, "The shortcut listener isn't running.")
        done = threading.Event()
        box: dict = {}
        self._cmds.put((combo, done, box))
        user32.PostMessageW(self._hwnd, WM_REGISTER_HOTKEY, 0, 0)
        if not done.wait(3):
            return HotKeyResult(False, "Timed out while registering the shortcut.")
        return box["result"]

    def _handle_register(self) -> None:
        while True:
            try:
                combo, done, box = self._cmds.get_nowait()
            except queue.Empty:
                return
            previous = self._hotkey_active
            user32.UnregisterHotKey(self._hwnd, HOTKEY_ID)
            if user32.RegisterHotKey(self._hwnd, HOTKEY_ID, combo.modifiers | MOD_NOREPEAT, combo.vk):
                self._hotkey_active = combo
                box["result"] = HotKeyResult(True)
            else:
                err = ctypes.get_last_error()
                if previous:                                # keep the old shortcut working
                    user32.RegisterHotKey(self._hwnd, HOTKEY_ID, previous.modifiers | MOD_NOREPEAT, previous.vk)
                if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                    msg = (f"{combo.display} is already used by another application or by Windows. "
                           "Choose a different shortcut in Settings.")
                else:
                    msg = f"Windows refused the shortcut {combo.display} (error {err}). Choose a different one in Settings."
                box["result"] = HotKeyResult(False, msg)
            done.set()

    # -------------------------------------------------------------- reading

    def _process_info(self, hwnd) -> tuple[Optional[str], Optional[str], bool]:
        """(friendly name, exe id, is_this_process)"""
        try:
            pid = wt.DWORD(0)
            if hwnd:
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return None, None, False
            if pid.value == kernel32.GetCurrentProcessId():
                return None, None, True
            h = kernel32.OpenProcess(0x1000, False, pid.value)      # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return None, None, False
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wt.DWORD(1024)
                if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return None, None, False
                path = buf.value
            finally:
                kernel32.CloseHandle(h)
            exe = Path(path).name.lower()
            if path not in self._name_cache:
                self._name_cache[path] = _file_description(path) or Path(path).stem.replace("_", " ").title()
            return self._name_cache[path], exe, False
        except Exception:
            log.debug("process lookup failed", exc_info=True)
            return None, None, False

    def read_clipboard(self, include_own: bool = False) -> Optional[ClipboardSnapshot]:
        owner = user32.GetClipboardOwner()
        if not include_own and owner and self._hwnd and owner == self._hwnd:
            return ClipboardSnapshot(ours=True)
        app, app_id, is_self = self._process_info(owner or user32.GetForegroundWindow())
        if is_self and not include_own:
            return ClipboardSnapshot(ours=True)
        snap = ClipboardSnapshot(source_app=app, source_id=app_id)
        try:
            with _clipboard():
                names = _format_names()
                present = set(names.values())
                if present & _EXCLUDE_FORMATS:
                    snap.excluded = True
                    return snap
                if _NO_HISTORY_FORMAT in present:
                    fmt = next(f for f, n in names.items() if n == _NO_HISTORY_FORMAT)
                    raw = _get_bytes(fmt)
                    if raw is not None and raw[:4] == b"\x00\x00\x00\x00":
                        snap.excluded = True
                        return snap
                if CF_HDROP in names:
                    snap.file_paths = self._read_files()
                if CF_UNICODETEXT in names:
                    raw = _get_bytes(CF_UNICODETEXT)
                    if raw is not None:
                        snap.text = _normalize_newlines(raw.decode("utf-16-le", "replace").split("\x00", 1)[0])
                if CF_RTF in names:
                    snap.rtf = (_get_bytes(CF_RTF) or b"").rstrip(b"\x00") or None
                if CF_HTML in names:
                    snap.html = (_get_bytes(CF_HTML) or b"").rstrip(b"\x00") or None
                if not snap.text and not snap.file_paths:
                    self._read_image(snap, names)
        except _ClipboardBusy:
            log.warning("clipboard stayed busy; skipping this change")
            return None
        return snap

    @staticmethod
    def _read_files() -> list[str]:
        h = user32.GetClipboardData(CF_HDROP)
        if not h:
            return []
        count = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
        paths = []
        for i in range(count):
            n = shell32.DragQueryFileW(h, i, None, 0)
            buf = ctypes.create_unicode_buffer(n + 1)
            shell32.DragQueryFileW(h, i, buf, n + 1)
            paths.append(buf.value)
        return paths

    @staticmethod
    def _read_image(snap: ClipboardSnapshot, names: dict[int, str]) -> None:
        if CF_PNG in names:
            png = _get_bytes(CF_PNG)
            if png:
                w, h = images.png_size(png)
                if w:
                    snap.image_png, snap.image_width, snap.image_height = png, w, h
                    return
        for fmt in (CF_DIB, CF_DIBV5):
            if fmt in names:
                dib = _get_bytes(fmt)
                conv = images.dib_to_png(dib) if dib else None
                if conv:
                    snap.image_png, snap.image_width, snap.image_height = conv
                    return

    # -------------------------------------------------------------- writing

    def write_clipboard(self, payload: ClipPayload, plain: bool = False) -> bool:
        try:
            with _clipboard(self._hwnd):
                user32.EmptyClipboard()
                if payload.kind == ContentKind.IMAGE:
                    if not payload.image_path:
                        return False
                    png = Path(payload.image_path).read_bytes()
                    ok = _set_bytes(CF_PNG, png)
                    dib = images.png_to_dib(png)
                    if dib:
                        ok = _set_bytes(CF_DIB, dib) or ok
                    return ok
                if payload.kind == ContentKind.FILE:
                    existing = [p for p in payload.file_paths if os.path.exists(p)]
                    if existing:
                        wide = ("\0".join(existing) + "\0\0").encode("utf-16-le")
                        head = _DROPFILES(ctypes.sizeof(_DROPFILES), 0, 0, 0, 1)
                        _set_bytes(CF_HDROP, bytes(head) + wide)
                        _set_bytes(CF_DROPEFFECT, (1).to_bytes(4, "little"))
                    return self._set_text(payload.text or "") or bool(existing)
                ok = self._set_text(payload.text or "")
                if not plain:
                    if payload.rtf:
                        _set_bytes(CF_RTF, payload.rtf + b"\x00")
                    if payload.html:
                        _set_bytes(CF_HTML, payload.html + b"\x00")
                return ok
        except _ClipboardBusy:
            return False
        except OSError:
            log.exception("couldn't read the stored image")
            return False

    def write_text(self, text: str) -> bool:
        try:
            with _clipboard(self._hwnd):
                user32.EmptyClipboard()
                return self._set_text(text)
        except _ClipboardBusy:
            return False

    @staticmethod
    def _set_text(text: str) -> bool:
        data = (text.replace("\r\n", "\n").replace("\n", "\r\n") + "\0").encode("utf-16-le", "surrogatepass")
        return _set_bytes(CF_UNICODETEXT, data)

    # -------------------------------------------------------------- focus / autostart

    def bring_to_front(self, hwnd: int) -> None:
        try:
            fg = user32.GetForegroundWindow()
            cur = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(fg_tid) and fg_tid != cur and user32.AttachThreadInput(cur, fg_tid, True)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            if attached:
                user32.AttachThreadInput(cur, fg_tid, False)
        except Exception:
            log.debug("bring_to_front failed", exc_info=True)

    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _RUN_NAME = "ClipboardMemory"

    @staticmethod
    def launch_command() -> str:
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" --background'
        pyw = Path(sys.executable).with_name("pythonw.exe")
        return f'"{pyw if pyw.exists() else sys.executable}" -m clipboard_memory --background'

    def autostart_enabled(self) -> bool:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY) as k:
                winreg.QueryValueEx(k, self._RUN_NAME)
                return True
        except OSError:
            return False

    def set_autostart(self, enabled: bool) -> Optional[str]:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                if enabled:
                    winreg.SetValueEx(k, self._RUN_NAME, 0, winreg.REG_SZ, self.launch_command())
                else:
                    try:
                        winreg.DeleteValue(k, self._RUN_NAME)
                    except FileNotFoundError:
                        pass
            return None
        except OSError as e:
            return ("Couldn't change the startup setting. Your organisation's policy may block it "
                    f"({e.__class__.__name__}).")
