"""Bounded, non-executable display IPC. No credentials or game operations."""
from __future__ import annotations

import os
from pathlib import Path
import re
import time
from urllib.parse import quote


class GameHUD:
    def __init__(self, game_dir: Path, clock=time.time):
        self.root = game_dir
        self.clock = clock
        self.enabled = False
        self.expanded = True
        self.seq = 0
        self.kind = "idle"
        self.text = "游戏内 AI 面板已连接。\n按 F9 分析当前手牌；F10 展开/收起。\n拖动标题移动，滚轮查看长建议。\nAPI 设置仍在任务栏托盘的助手菜单中。"
        self.fingerprint = ""
        self.acknowledged = False
        self.supports_blocks = False

    def ready(self):
        try:
            path = self.root / "balatro_copilot_hud_ready.txt"
            if path.stat().st_size > 256:
                return None
            parts = path.read_text(encoding="ascii").strip().split("|")
            if len(parts) not in (6, 7) or parts[0] != "BACP_READY_V1":
                return None
            if len(parts) == 7 and parts[6] != "BACP_BLOCKS_V1":
                return None
            timestamp, draws, seq, expanded = map(int, parts[1:5])
            if not 0 <= self.clock() - timestamp < 5 or draws <= 0 or seq < 0 or parts[5] != "ok":
                return None
            if seq == self.seq:
                self.acknowledged = True
            if not self.acknowledged:
                return None
            self.supports_blocks = len(parts) == 7
            if seq == self.seq and expanded in (0, 1):
                self.expanded = bool(expanded)
            return dict(draws=draws, seq=seq, expanded=expanded, supports_blocks=self.supports_blocks)
        except (OSError, UnicodeError, ValueError):
            return None

    def message(self, kind: str, text: str, fingerprint: str = ""):
        if kind not in {"idle", "busy", "result", "error", "stopped"}:
            raise ValueError("Unknown HUD message")
        if kind == "result" and not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            kind, text, fingerprint = "error", "游戏内面板尚未加载：请安全退出并重启 Balatro，然后重新按 F9。", ""
        self.kind = kind
        self.text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32)[:5000]
        self.fingerprint = fingerprint
        self.expanded = True
        self.seq += 1
        self.flush()

    def toggle(self):
        self.ready()
        self.expanded = not self.expanded
        self.seq += 1
        self.flush()

    def flush(self):
        if not self.enabled:
            return
        # Fixed-position fields; no JSON/Lua evaluation in the game.
        body = quote(self.text, safe="")
        data = "\n".join(("BACP_HUD_V1", str(int(self.clock())), str(self.seq),
                          "1" if self.expanded else "0", self.kind,
                          self.fingerprint, body)) + "\n"
        if len(data.encode("ascii")) > 64000:
            raise ValueError("HUD message too large")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "balatro_copilot_hud.txt"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(data, encoding="ascii", newline="\n")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows game reader can briefly deny replace while reading.
                # Retry a bounded <150ms; the GUI heartbeat continues recovery.
                if attempt == 4:
                    raise
                time.sleep(0.015 * (attempt+1))

