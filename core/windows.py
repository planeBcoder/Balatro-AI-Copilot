"""Small Windows API boundary: process detection, hotkeys, export keystroke."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from pathlib import Path

from core.errors import CopilotError

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetForegroundWindow.restype = w.HWND
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.GetWindow.argtypes = [w.HWND, w.UINT]
user32.GetWindow.restype = w.HWND
user32.GetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
user32.SetWindowPos.restype = w.BOOL
kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.OpenProcess.restype = w.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = w.BOOL
kernel32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = w.HANDLE
kernel32.CloseHandle.argtypes = [w.HANDLE]


class ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG), ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260)]


kernel32.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel32.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]


def process_pids(executable: str) -> set[int]:
    handle = kernel32.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise CopilotError("无法检测游戏进程，请重试。")
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    pids: set[int] = set()
    try:
        ok = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == executable.lower():
                pids.add(entry.th32ProcessID)
            ok = kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return pids


def balatro_pids() -> set[int]:
    return process_pids('Balatro.exe')


def foreground_pid() -> int:
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value


class KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyboardInput), ("mi", MouseInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", InputUnion)]


user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
user32.SendInput.restype = w.UINT


def export_f8(pids: set[int]) -> None:
    # Never type into an unrelated app or change foreground focus.
    if foreground_pid() not in pids:
        raise CopilotError("请回到 Balatro 中按 F9。")
    inputs = (Input * 2)()
    for i in range(2):
        inputs[i].type = 1
        inputs[i].ki = KeyboardInput(0x77, 0, 0 if i == 0 else 2, 0, 0)
    if user32.SendInput(2, inputs, ctypes.sizeof(Input)) != 2:
        raise CopilotError("状态刷新失败，请重试。")


def no_activate(hwnd: int) -> None:
    get_long = user32.GetWindowLongPtrW
    set_long = user32.SetWindowLongPtrW
    get_long.argtypes = [w.HWND, ctypes.c_int]
    get_long.restype = ctypes.c_ssize_t
    set_long.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_long.restype = ctypes.c_ssize_t
    flags = get_long(hwnd, -20)
    set_long(hwnd, -20, flags | 0x08000000 | 0x00000080)  # NOACTIVATE | TOOLWINDOW


def pin_topmost(hwnd: int) -> bool:
    """Reorder only our overlay; never activate, resize, move or show it."""
    return bool(user32.SetWindowPos(hwnd, w.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0200))


def window_above(hwnd: int, other: int) -> bool:
    """Read actual desktop Z order rather than assuming WS_EX_TOPMOST suffices."""
    previous = user32.GetWindow(other, 3)  # GW_HWNDPREV
    for _ in range(1024):
        if not previous:
            return False
        if previous == hwnd:
            return True
        previous = user32.GetWindow(previous, 3)
    return False


def foreground_game_window() -> int | None:
    """Cheap read-only foreground lookup; does not scan cards or send input."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = w.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            if Path(buffer.value).name.lower() == "balatro.exe":
                return hwnd
    finally:
        kernel32.CloseHandle(handle)
    return None
