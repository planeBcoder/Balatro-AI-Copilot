from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import sys
import winreg

APP_DIR = Path(os.environ["LOCALAPPDATA"]) / "BalatroAICopilot"
GAME_DIR = Path(os.environ["APPDATA"]) / "Balatro"


def resource(relative: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / relative


class Blob(ctypes.Structure):
    _fields_ = [("cbData", w.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data: bytes, decrypt: bool = False) -> bytes:
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    src, dst = Blob(len(data), buf), Blob()
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(Blob)]
    fn.restype = w.BOOL
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise OSError("Windows 密钥加密失败。")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel.LocalFree(dst.pbData)


@dataclass
class Settings:
    game_install: str = ''
    model: str = "deepseek-flash"
    simulation_count: int = 2000
    x: int = -1
    y: int = -1
    startup: bool = False
    api_verified: bool = False
    display_compatibility: bool = False
    in_game_hud: bool = False
    auto_play_enabled: bool = False


class SettingsStore:
    def __init__(self, root: Path = APP_DIR):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "settings.json"
        self.key_path = root / "key.dpapi"

    def load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            s = Settings(**{k: v for k, v in raw.items() if k in Settings.__dataclass_fields__})
            if not isinstance(s.game_install,str) or len(s.game_install)>32767:
                s.game_install=''
            if s.model not in {"deepseek-flash", "deepseek-v4-pro"}:
                s.model = "deepseek-flash"
            s.simulation_count = max(500, min(20000, int(s.simulation_count)))
            s.display_compatibility = s.display_compatibility is True
            s.in_game_hud = s.in_game_hud is True
            s.auto_play_enabled = s.auto_play_enabled is True
            if s.in_game_hud:
                s.display_compatibility = False
            return s
        except (OSError, ValueError, TypeError):
            return Settings()

    def save(self, s: Settings) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def key(self) -> str:
        try:
            return protect(self.key_path.read_bytes(), True).decode("utf-8")
        except (OSError, UnicodeError):
            return ""

    def save_key(self, value: str) -> None:
        tmp = self.key_path.with_suffix(".tmp")
        tmp.write_bytes(protect(value.strip().encode("utf-8")))
        os.replace(tmp, self.key_path)


def set_startup(enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            if getattr(sys, "frozen", False):
                command = f'"{sys.executable}"'
            else:
                command = f'"{sys.executable}" "{resource("main.py")}"'
            winreg.SetValueEx(key, "BalatroAICopilot", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "BalatroAICopilot")
            except FileNotFoundError:
                pass


def ensure_bridge() -> str:
    """Extend only our known exporter; backup before writing, no save access."""
    path = GAME_DIR / "Mods" / "BalatroStateExporter" / "main.lua"
    try:
        src = path.read_text(encoding="utf-8")
        if "-- BALATRO_COPILOT_BRIDGE_V1" in src:
            return "ready"
        if "-- Balatro State Exporter 0.1.0" not in src or "local function collect_state()" not in src:
            return "legacy"
        backup = path.with_name("main.lua.pre-copilot.bak")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        extension = resource("assets/exporter_extension.lua").read_text(encoding="utf-8")
        tmp = path.with_suffix(".copilot.tmp")
        tmp.write_text(src + "\n" + extension, encoding="utf-8")
        os.replace(tmp, path)
        return "installed"
    except OSError:
        logging.getLogger(__name__).exception("Exporter extension unavailable; F8 fallback")
        return "legacy"
