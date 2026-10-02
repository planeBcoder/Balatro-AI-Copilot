"""Per-Windows-session activation handshake; no sockets or game operations."""
import ctypes
from ctypes import wintypes as w

from core.windows import kernel32


class SingleInstance:
    def __init__(self, name="BalatroAICopilot"):
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
        kernel32.CreateMutexW.restype = w.HANDLE
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
        kernel32.CreateEventW.restype = w.HANDLE
        kernel32.SetEvent.argtypes = [w.HANDLE]
        kernel32.SetEvent.restype = w.BOOL
        kernel32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel32.WaitForSingleObject.restype = w.DWORD
        self.mutex = self.event = self.quit_event = None
        ctypes.set_last_error(0)
        self.mutex = kernel32.CreateMutexW(None, False, "Local\\" + name)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.existing = ctypes.get_last_error() == 183
        # Auto-reset event: a launch request survives until the UI starts polling.
        self.event = kernel32.CreateEventW(None, False, False, "Local\\" + name + "Open")
        if not self.event:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)
        self.quit_event = kernel32.CreateEventW(None, False, False, "Local\\" + name + "Quit")
        if not self.quit_event:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def request_open(self):
        if not kernel32.SetEvent(self.event):
            raise ctypes.WinError(ctypes.get_last_error())

    def requested(self):
        return kernel32.WaitForSingleObject(self.event, 0) == 0

    def request_quit(self):
        if not kernel32.SetEvent(self.quit_event):
            raise ctypes.WinError(ctypes.get_last_error())

    def quit_requested(self):
        return kernel32.WaitForSingleObject(self.quit_event, 0) == 0

    def close(self):
        for handle in (self.quit_event, self.event, self.mutex):
            if handle:
                kernel32.CloseHandle(handle)
        self.quit_event = self.event = self.mutex = None
