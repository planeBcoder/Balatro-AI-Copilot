from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import time
import uuid
from typing import Callable

from core.errors import CopilotError
from core.state_schema import State, parse_state

log = logging.getLogger(__name__)


class StateRefresher:
    def __init__(self, root: Path, detect: Callable[[], set[int]], trigger_f8: Callable[[set[int]], None], timeout: float = 3.0):
        self.root, self.detect, self.trigger_f8, self.timeout = root, detect, trigger_f8, timeout

    def refresh(self) -> State:
        pids = self.detect()
        log.info("Balatro process detection: %s", bool(pids))
        if not pids:
            raise CopilotError("未检测到 Balatro。")
        token = uuid.uuid4().hex
        bridge = self.root / "balatro_copilot_ready.txt"
        if bridge.exists():
            return self._request(token)
        return self._legacy(pids)

    def _request(self, token: str) -> State:
        # A nonce echoed by the game certifies that this request has been read.
        # It is stronger than timestamps, including requests in the same second.
        request = self.root / "balatro_copilot_request.txt"
        tmp = self.root / f"balatro_copilot_request.{token}.tmp"
        self.root.mkdir(parents=True, exist_ok=True)
        tmp.write_text(f"{token}|{int(time.time())}", encoding="ascii")
        try:
            for attempt in range(10):
                try:
                    os.replace(tmp, request)
                    break
                except OSError:
                    if attempt==9:raise
                    time.sleep(.025)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise CopilotError('状态请求文件短暂占用，请重试。') from None
        response = self.root / "balatro_copilot_response.json"
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                raw = response.read_bytes()
                data = json.loads(raw)
                if data.get("request_id") == token:
                    state = parse_state(raw)
                    log.info("State file refreshed: nonce acknowledged, %s", state.game_state)
                    return state
            except (OSError, ValueError):
                pass  # The game may still be writing; never read a partial snapshot.
            time.sleep(0.025)
        raise CopilotError("状态刷新失败，请重试。")

    def _legacy(self, pids: set[int]) -> State:
        path = self.root / "balatro_state.json"
        baseline = path.stat().st_mtime_ns if path.exists() else 0
        start_wall = time.time_ns()
        self.trigger_f8(pids)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                stat_before = path.stat()
                if stat_before.st_mtime_ns > baseline and stat_before.st_mtime_ns >= start_wall:
                    raw = path.read_bytes()
                    stat_after = path.stat()
                    if stat_before.st_mtime_ns == stat_after.st_mtime_ns and stat_before.st_size == stat_after.st_size:
                        state = parse_state(raw)
                        log.info("State file refreshed: F8 mtime confirmed, hash=%s", hashlib.sha256(raw).hexdigest()[:12])
                        return state
            except (OSError, CopilotError):
                pass
            time.sleep(0.025)
        raise CopilotError("状态刷新失败，请重试。")
