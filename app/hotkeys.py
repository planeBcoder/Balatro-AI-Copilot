from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

from core.windows import user32

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, analyze, toggle, auto=None, stop=None):
        super().__init__()
        self.callbacks = {901: analyze, 902: toggle}
        if auto is not None:
            self.callbacks[903] = auto
        if stop is not None:
            self.callbacks[904] = stop
        self.registered: list[int] = []

    def register(self) -> bool:
        for ident, vk in ((901, 0x78), (902, 0x79)):
            if not user32.RegisterHotKey(None, ident, 0x4000, vk):
                self.close()
                return False
            self.registered.append(ident)
        for ident, vk in ((903, 0x7A), (904, 0x7B)):
            if ident in self.callbacks and user32.RegisterHotKey(None, ident, 0x4000, vk):
                self.registered.append(ident)
        return True

    def nativeEventFilter(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x0312 and msg.wParam in self.callbacks:
            self.callbacks[msg.wParam]()
            return True, 0
        return False, 0

    def close(self):
        for ident in self.registered:
            user32.UnregisterHotKey(None, ident)
        self.registered.clear()
