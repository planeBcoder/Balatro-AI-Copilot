"""Opt-in current-Blind runner; typed file IPC, no Windows click injection."""
from __future__ import annotations
import os
from pathlib import Path
import re
import time
import uuid
from typing import Callable
from core.errors import CopilotError
from core.calculation import candidates
from core.decision_policy import local_decision


class AutoStopped(Exception):
    pass


class AutoTransport:
    def __init__(self, root: Path, clock=time.time):
        self.root, self.clock = root, clock
        self.token = uuid.uuid4().hex
        self.active = False

    def _write(self, filename, data):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / filename
        tmp = self.root / (filename+"."+uuid.uuid4().hex+".tmp")
        tmp.write_text(data, encoding="ascii", newline="\n")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.015*(attempt+1))

    def start(self):
        self.token = uuid.uuid4().hex
        self.active = True
        try:
            self.heartbeat()
        except OSError:
            self.active = False
            raise

    def heartbeat(self):
        self._write("balatro_copilot_auto_lease.txt", f"BACP_AUTO_LEASE_V1\n{self.token}\n{int(self.clock())}\n{int(self.active)}\n")

    def stop(self):
        self.active = False  # The thread sees this before a new command write.
        self.heartbeat()

    def execute(self, state, candidate: dict, cancelled: Callable[[], bool]):
        if cancelled() or not self.active:
            raise AutoStopped("自动已停止。")
        meta = (state.model_extra or {}).get("autoplay") or {}
        if meta.get("version") != 1 or not meta.get("ready") or meta.get("forced_selection"):
            raise CopilotError("自动执行接口尚未就绪或有强制选牌，请手动操作。")
        session, signature = meta.get("session", ""), meta.get("signature", "")
        if not all(isinstance(v,str) and re.fullmatch(r"[a-f0-9]{64}",v) for v in (session,signature)):
            raise CopilotError("自动执行局面标识无效。")
        ids = meta.get("card_ids", [])
        try:
            chosen = [ids[i] for i in candidate["indices"]]
        except (IndexError, TypeError):
            raise CopilotError("自动执行选牌标识不完整。") from None
        if not 1 <= len(chosen) <= 5 or len(set(chosen)) != len(chosen) or any(type(i) is not int or i <= 0 for i in chosen):
            raise CopilotError("自动执行选牌无效。")
        if candidate.get("action") not in {"play", "discard"}:
            raise CopilotError("自动模式不支持这个动作。")
        if candidate["cards"] != [state.hand[i].code for i in candidate["indices"]]:
            raise CopilotError("自动执行牌面不一致。")
        request_id = uuid.uuid4().hex
        command = "\n".join(("BACP_AUTO_COMMAND_V1",request_id,str(int(self.clock())),self.token,session,signature,candidate["action"],",".join(map(str,chosen))))+"\n"
        if cancelled() or not self.active:
            raise AutoStopped("自动已停止。")
        self._write("balatro_copilot_auto_command.txt",command)
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            if cancelled() or not self.active:
                raise AutoStopped("自动已停止；已提交的动作不能撤回。")
            try:
                path = self.root / "balatro_copilot_auto_ack.txt"
                if path.stat().st_size <= 256:
                    fields = path.read_text(encoding="ascii").strip().split("|")
                    if len(fields)==4 and fields[:2]==["BACP_AUTO_ACK_V1",request_id]:
                        if fields[2:] == ["accepted","invoked"]:
                            return
                        raise CopilotError("游戏拒绝或未确认该动作，自动已停止。不会重复出牌。")
            except (OSError, UnicodeError):
                pass
            time.sleep(.04)
        raise CopilotError("动作结果未确认，自动已停止。请核对游戏；不会重试该动作。")


def execution_metadata(state):
    return (state.model_extra or {}).get("autoplay") or {}


class AutoRunner:
    MAX_ACTIONS = 30
    MAX_MODEL_DECISIONS = 10  # Client can make <=2 HTTP attempts per decision.
    TRANSIENT = {"HAND_PLAYED", "DRAW_TO_HAND"}

    def __init__(self, refresh, client, transport, samples, cancelled, status=lambda _:None, result=lambda _:None):
        self.refresh,self.client,self.transport,self.samples = refresh,client,transport,samples
        self.cancelled,self.status,self.result = cancelled,status,result
        self.model_decisions = 0

    def check_stop(self):
        if self.cancelled() or not self.transport.active:
            raise AutoStopped("自动已停止。")

    def pause(self, seconds):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            self.check_stop()
            time.sleep(min(.05,max(0,deadline-time.monotonic())))

    def wait_ready(self, initial=False):
        deadline=time.monotonic()+(2 if initial else 18)
        while time.monotonic()<deadline:
            self.check_stop()
            try:
                state=self.refresh()
            except CopilotError:
                if initial:
                    raise
                self.pause(.2)
                continue  # Retry reading only, never the already submitted action.
            meta=execution_metadata(state)
            if meta.get("version") != 1:
                raise CopilotError("自动模组尚未加载，请正常退出并重启 Balatro 一次。")
            if meta.get("cancelled_run")==self.transport.token:
                raise AutoStopped("游戏内已按 Escape 取消自动。")
            if state.game_state=="SELECTING_HAND" and meta.get("ready"):
                if meta.get("forced_selection"):
                    raise CopilotError("当前有强制选牌，自动模式暂停，请手动处理。")
                return state
            if state.game_state not in self.TRANSIENT | {"SELECTING_HAND"}:
                raise AutoStopped("当前盲注已结束或进入其他界面，自动已停止；请自行结算和购物。" if not initial else "请进入可选手牌的牌局，再按 F11。")
            self.pause(.2)
        raise CopilotError("游戏等待超时，自动已停止。")

    def run(self):
        state=self.wait_ready(True)
        blind=execution_metadata(state).get("blind_id")
        session=execution_metadata(state).get("session")
        if not blind or not session:
            raise CopilotError("缺少自动运行边界标识。")
        for step in range(self.MAX_ACTIONS):
            self.check_stop()
            meta=execution_metadata(state)
            if meta.get("blind_id")!=blind or meta.get("session")!=session:
                raise AutoStopped("盲注或游戏进程已变化，自动已停止。")
            started=time.perf_counter()
            self.status(f"自动 {step+1} · 正在判断；F12 / Escape 停止")
            calc=candidates(state,self.samples)
            plays=[c for c in calc["candidates"] if c["action"]=="play"]
            if not plays or calc.get("shortfall") is None or calc.get("score_limitations") or any(not c.get("score_certified") for c in plays):
                raise CopilotError("当前效果无法完整验算，自动暂停；可按 F9 查看人工参考建议。")
            decision=local_decision(calc)
            if decision is None:
                if self.model_decisions>=self.MAX_MODEL_DECISIONS:
                    raise AutoStopped("本次已达 10 次模型决策上限，自动已停止。")
                self.model_decisions+=1
                decision=self.client.decide(state.ai_payload(),calc)
            self.check_stop()
            candidate=next((c for c in calc["candidates"] if c["id"]==decision.recommendations[0].candidate_id),None)
            if candidate is None:
                raise CopilotError("自动决策候选无效。")
            self.result({"state":state,"calculation":calc,"decision":decision,"elapsed":time.perf_counter()-started,"auto_pending":True})
            self.pause(.9)  # Visible preview, user can cancel before dispatch.
            current=self.refresh()
            fresh=execution_metadata(current)
            if current.game_state!="SELECTING_HAND" or not fresh.get("ready") or fresh.get("signature")!=meta.get("signature"):
                raise AutoStopped("局面或选牌已变化，旧决策未执行；请重新按 F11。")
            self.transport.execute(current,candidate,self.cancelled)
            # ACK only proves callback invocation. Confirm resource consumption
            # before another action; never treat selection as completed play.
            deadline=time.monotonic()+18
            observed=False
            while time.monotonic()<deadline:
                self.check_stop()
                try:
                    current=self.refresh()
                except CopilotError:
                    self.pause(.2)
                    continue
                before=state.hands_left if candidate["action"]=="play" else state.discards_left
                after=current.hands_left if candidate["action"]=="play" else current.discards_left
                if after==before-1:
                    observed=True
                    break
                if after!=before:
                    raise AutoStopped("资源变化与预计不符，自动已停止，请核对游戏。")
                if current.game_state not in self.TRANSIENT | {"SELECTING_HAND"}:
                    raise AutoStopped("游戏已转入结算或其他界面，自动已停止。")
                self.pause(.2)
            if not observed:
                raise CopilotError("动作未产生可确认的资源变化，自动停止，不重复执行。")
            self.pause(.3)
            state=self.wait_ready()
        raise AutoStopped("本次已达 30 个动作上限，自动已停止。")
