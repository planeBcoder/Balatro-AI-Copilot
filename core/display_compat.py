"""Opt-in one-pixel borderless workaround; no game code, inputs or save writes."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import zlib

from core.windows import user32, kernel32, foreground_game_window

log = logging.getLogger(__name__)
Rect = tuple[int, int, int, int]


@dataclass
class WindowSnapshot:
    hwnd: int
    pid: int
    rect: Rect
    monitor: Rect
    borderless: bool
    interactive: bool = True


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]


user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.GetWindowRect.restype = w.BOOL
user32.IsWindow.argtypes = [w.HWND]
user32.IsWindowVisible.argtypes = [w.HWND]
user32.IsIconic.argtypes = [w.HWND]
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
user32.MonitorFromWindow.restype = w.HANDLE
user32.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.POINTER(MonitorInfo)]
user32.GetMonitorInfoW.restype = w.BOOL


def rect_tuple(rect: w.RECT) -> Rect:
    return rect.left, rect.top, rect.right, rect.bottom


class WindowsDisplay:
    def foreground(self):
        return foreground_game_window()

    def snapshot(self, hwnd: int) -> WindowSnapshot | None:
        if not user32.IsWindow(hwnd):
            return None
        classname = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, classname, len(classname))
        if classname.value != "SDL_app":
            return None
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None
        try:
            name, count = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(count)):
                return None
            if Path(name.value).name.lower() != "balatro.exe":
                return None
        finally:
            kernel32.CloseHandle(handle)
        rect, monitor = w.RECT(), MonitorInfo()
        monitor.cbSize = ctypes.sizeof(monitor)
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(monitor)):
            return None
        style = user32.GetWindowLongPtrW(hwnd, -16)
        return WindowSnapshot(hwnd, pid.value, rect_tuple(rect), rect_tuple(monitor.rcMonitor),
            bool(style & 0x80000000) and not bool(style & 0x00C00000),
            bool(user32.IsWindowVisible(hwnd)) and not bool(user32.IsIconic(hwnd)))

    def resize(self, hwnd: int, rect: Rect) -> bool:
        left, top, right, bottom = rect
        # NOZORDER | NOACTIVATE | NOOWNERZORDER. Permit the verified 1px downward
        # offset so the compatibility gap is at the top, not over the taskbar.
        return bool(user32.SetWindowPos(hwnd, None, left, top, right-left, bottom-top, 0x0214))


class DisplayCompatibility:
    def __init__(self, root: Path, game_dir: Path, backend=None):
        self.path = root / "display_restore.json"
        self.settings_path = game_dir / "settings.jkr"
        self.backend = backend or WindowsDisplay()
        self.enabled = False
        self.record = None
        self.mode_cache = (None, False)
        self.failed = set()
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            self.record = self.validate(record)
        except (OSError, ValueError, TypeError, KeyError):
            pass

    @staticmethod
    def validate(record):
        if not isinstance(record, dict):
            raise ValueError("Invalid restore record")
        for key in ("hwnd", "pid"):
            if type(record[key]) is not int or record[key] <= 0:
                raise ValueError("Invalid identity")
        for key in ("original", "adjusted"):
            values = record[key]
            if not isinstance(values, list) or len(values) != 4 or any(type(v) is not int or abs(v) > 100000 for v in values):
                raise ValueError("Invalid rectangle")
        original, adjusted = record["original"], record["adjusted"]
        legacy_bottom_gap = original[:3] + [original[3]-1]
        top_gap = [original[0], original[1]+1, original[2], original[3]]
        if adjusted not in (legacy_bottom_gap, top_gap) or original[2]-original[0] < 100 or original[3]-original[1] < 100:
            raise ValueError("Not a one-pixel adjustment")
        return record

    def borderless_mode(self):
        """Read only a bounded, compressed display preference; never execute Lua."""
        try:
            stat = self.settings_path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature == self.mode_cache[0]:
                return self.mode_cache[1]
            if stat.st_size > 2_000_000:
                return False
            raw = self.settings_path.read_bytes()
            text = None
            for bits in (15, -15, 31):
                try:
                    decoder = zlib.decompressobj(bits)
                    value = decoder.decompress(raw, 8_000_000)
                    if decoder.eof:
                        text = value.decode("utf-8")
                        break
                except (zlib.error, UnicodeError):
                    pass
            match = re.search(r'\["screenmode"\]\s*=\s*"([^"]+)"', text or "")
            result = bool(match and match.group(1) == "Borderless")
            self.mode_cache = (signature, result)
            return result
        except OSError:
            return False

    def persist(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(tmp, self.path)

    def forget(self):
        self.record = None
        self.path.unlink(missing_ok=True)

    def owned_snapshot(self):
        if not self.record:
            return None
        window = self.backend.snapshot(self.record["hwnd"])
        if window is None or window.pid != self.record["pid"]:
            self.forget()
            return None
        if not window.interactive:
            return None  # Keep the journal for when the game is restored.
        if (window.rect != tuple(self.record["adjusted"]) or window.monitor != tuple(self.record["original"])
                or not window.borderless or not self.borderless_mode()):
            self.forget()  # Respect a user's later size/mode/monitor change.
            return None
        return window

    def restore(self):
        window = self.owned_snapshot()
        if window:
            original = tuple(self.record["original"])
            if self.backend.resize(window.hwnd, original):
                updated = self.backend.snapshot(window.hwnd)
                if updated and updated.pid == window.pid and updated.rect == original:
                    log.info("Display compatibility restored: window=%s", window.hwnd)
                    self.forget()
                    return True
            log.warning("Display compatibility restore pending: window=%s", window.hwnd)
        return self.record is None

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.failed.clear()
        self.tick()

    def tick(self):
        if self.record:
            if self.enabled:
                window = self.owned_snapshot()
                if window and self.record["adjusted"][1] == self.record["original"][1]:
                    # Safely migrate a surviving 1.0.3 bottom-gap journal.
                    self.restore()
            else:
                self.restore()
        if not self.enabled or self.record:
            return
        hwnd = self.backend.foreground()
        if not hwnd or not self.borderless_mode():
            return
        window = self.backend.snapshot(hwnd)
        if not window or not window.interactive or not window.borderless or window.rect != window.monitor:
            return
        identity = (hwnd, window.pid, window.rect)
        if identity in self.failed:
            return
        left, top, right, bottom = window.rect
        if right-left < 100 or bottom-top < 100:
            return
        adjusted = (left, top+1, right, bottom)
        record = dict(hwnd=hwnd, pid=window.pid, original=list(window.rect), adjusted=list(adjusted))
        self.persist(record)  # Recovery information exists BEFORE changing the window.
        self.record = record
        if self.backend.resize(hwnd, adjusted):
            updated = self.backend.snapshot(hwnd)
            if updated and updated.pid == window.pid and updated.rect == adjusted:
                log.info("Display compatibility applied: %sx%s -> %sx%s top_gap=1px bottom_covered=True window=%s", right-left, bottom-top, right-left, bottom-top-1, hwnd)
                return
        self.failed.add(identity)
        self.owned_snapshot()
        log.warning("Display compatibility resize failed or ignored: window=%s", hwnd)
