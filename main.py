from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as w
import json
import logging
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

from ai.deepseek_client import DeepSeekClient
from app.hotkeys import Hotkeys
from app.overlay import Overlay
from app.settings_dialog import SettingsDialog
from config.logging_config import setup, Redact
from config.settings import SettingsStore, APP_DIR, GAME_DIR, resource, ensure_bridge
from core.calculation import candidates
from core.decision_policy import local_decision
from core.errors import CopilotError
from core.state_refresh import StateRefresher
from core.single_instance import SingleInstance
from core.display_compat import DisplayCompatibility
from core.game_hud import GameHUD
from core.hud_install import install_hud, upgrade_fingerprint, upgrade_scoring_snapshot, install_autoplay, upgrade_numeric_precision, upgrade_shop_snapshot
from core.autoplay import AutoTransport, AutoRunner, AutoStopped
from core.windows import balatro_pids, export_f8, kernel32, user32, Input, KeyboardInput

log = logging.getLogger(__name__)


class Worker(QThread):
    stage = Signal(str)
    result = Signal(object)
    error = Signal(str)

    def __init__(self, key, settings, verify=False):
        super().__init__()
        self.key, self.model, self.samples = key, settings.model, settings.simulation_count
        self.verify = verify

    def run(self):
        try:
            total_start = time.perf_counter()
            client = DeepSeekClient(self.key, self.model, resource("prompts/decision_system.md"))
            if self.verify:
                client.smoke()
                self.result.emit({"verified": True})
                return
            start = time.perf_counter()
            state = StateRefresher(GAME_DIR, balatro_pids, export_f8).refresh()
            refresh_ms = (time.perf_counter() - start) * 1000
            if state.game_state == 'SHOP':
                from core.shop import shop_candidates, shop_ai_payload, local_shop_decision
                self.stage.emit('本地核算商店价格、槽位和构筑收益…')
                calc=shop_candidates(state)
                decision=local_shop_decision(calc)
                calc['ai_reviewed']=False
                if self.key:
                    self.stage.emit('本地核算完成，AI 比较经济与构筑取舍…')
                    try:
                        decision=client.decide_shop(shop_ai_payload(state),calc)
                        calc['ai_reviewed']=True
                    except CopilotError as exc:
                        calc['ai_error']=str(exc)
                else:
                    calc['ai_error']='未填写 API Key，当前为本地建议。'
                if self.isInterruptionRequested():return
                fresh=StateRefresher(GAME_DIR,balatro_pids,export_f8).refresh()
                if not getattr(state,'hud_fingerprint','') or getattr(fresh,'hud_fingerprint','')!=state.hud_fingerprint:
                    raise CopilotError('分析期间商店已变化，旧建议已丢弃。请在当前商店重新按 F9。')
                self.result.emit({'state':state,'calculation':calc,'decision':decision,'elapsed':time.perf_counter()-total_start})
                return
            if state.game_state != "SELECTING_HAND":
                raise CopilotError(f"当前状态暂不支持：{state.game_state}。请进入可选手牌的牌局。")
            self.stage.emit("正在分析…")
            start = time.perf_counter()
            calculation = candidates(state, self.samples)
            if not calculation.get('immediate_finish_ids'):
                from core.fullrun import annotate_general_draws
                annotate_general_draws(state, calculation)
            local_ms = (time.perf_counter() - start) * 1000
            if not calculation["candidates"]:
                raise CopilotError("当前局面没有可验证的出牌或弃牌操作。")
            if self.isInterruptionRequested():
                return
            start = time.perf_counter()
            decision = local_decision(calculation)
            if decision is None:
                self.stage.emit("正在比较出牌与弃牌收益…")
                if not self.key:raise CopilotError('当前打法需要 AI 比较，请在设置填写 API Key。')
                decision = client.decide(state.ai_payload(), calculation)
            api_ms = (time.perf_counter() - start) * 1000
            elapsed = time.perf_counter() - total_start
            log.info("Analysis latency: refresh_ms=%.1f local_ms=%.1f api_ms=%.1f total_ms=%.1f", refresh_ms, local_ms, api_ms, elapsed * 1000)
            self.result.emit({"state": state, "calculation": calculation, "decision": decision, "elapsed": elapsed})
        except CopilotError as exc:
            log.info("User-facing failure: %s", exc)
            self.error.emit(str(exc))
        except Exception:
            log.exception("Unexpected worker exception")
            self.error.emit("分析暂时失败，请重试。详细信息已保存在本机日志。")


class StateProbe(QThread):
    result = Signal(object)
    error = Signal(str)

    def run(self):
        try:
            start = time.perf_counter()
            s = StateRefresher(GAME_DIR, balatro_pids, export_f8).refresh()
            self.result.emit({"state": s.game_state, "nonce_acknowledged": bool(s.request_id), "hand_count": len(s.hand), "deck_count": len(s.deck_remaining), "refresh_ms": round((time.perf_counter() - start) * 1000, 1)})
        except CopilotError as exc:
            self.error.emit(str(exc))


class AutoWorker(QThread):
    stage = Signal(str)
    recommendation = Signal(object)
    stopped = Signal(str)

    def __init__(self, key, settings, transport):
        super().__init__()
        self.key,self.settings,self.transport = key,settings,transport

    def run(self):
        try:
            refresher = StateRefresher(GAME_DIR, balatro_pids, lambda _: None)
            client = DeepSeekClient(self.key,self.settings.model,resource("prompts/decision_system.md"))
            AutoRunner(refresher.refresh,client,self.transport,self.settings.simulation_count,
                       self.isInterruptionRequested,self.stage.emit,self.recommendation.emit).run()
        except (AutoStopped,CopilotError) as exc:
            self.stopped.emit(str(exc))
        except Exception:
            log.exception("Unexpected autoplay error; no action retries")
            self.stopped.emit("自动模式发生异常，已停止。请核对游戏，动作不会重试。")


def icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#172b35"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(1, 1, 62, 62, 13, 13)
    painter.setPen(QColor("#87dccb"))
    font = painter.font()
    font.setPixelSize(26)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "AI")
    painter.end()
    return QIcon(pixmap)


class Controller:
    def __init__(self, app: QApplication, smoke: Path | None = None):
        self.app, self.smoke = app, smoke
        self.store = SettingsStore(smoke.parent / "smoke-profile" if smoke else APP_DIR)
        self.settings = self.store.load()
        setup(self.store.root)
        log.info("App start: version=1.5.0 frozen=%s", bool(getattr(sys, "frozen", False)))
        self.worker = None
        self.auto_worker = None
        self.auto_transport = AutoTransport(smoke.parent / "smoke-game" if smoke else GAME_DIR)
        self.auto_timer = QTimer(app)
        self.auto_timer.setInterval(600)
        self.auto_timer.timeout.connect(self.auto_heartbeat)
        self.pending_exit = False
        self.dialog = None
        self.hotkey_toggles = 0
        self.hotkey_analyses = 0
        self.smoke_refresh_result = None
        self.hud = GameHUD(smoke.parent / "smoke-game" if smoke else GAME_DIR)
        self.hud_attached = False
        self.hud_last_write = 0
        self.hud_failures = 0
        self.hud_upgrade_notice = False
        self.hud_timer = QTimer(app)
        self.hud_timer.setInterval(500)
        self.hud_timer.timeout.connect(self.hud_tick)
        self.display_compatibility = DisplayCompatibility(self.store.root, GAME_DIR)
        self.display_timer = QTimer(app)
        self.display_timer.setInterval(500)
        self.display_timer.timeout.connect(self.display_tick)
        if not smoke:
            self.display_compatibility.enabled = self.settings.display_compatibility
            self.display_timer.start()
        self.overlay = Overlay()
        self.overlay.analyze_requested.connect(self.analyze)
        self.overlay.settings_requested.connect(self.open_settings)
        self.overlay.copy_requested.connect(self.copy)
        self.overlay.position_changed.connect(self.save_position)
        area = app.primaryScreen().availableGeometry()
        self.overlay.move(self.settings.x if self.settings.x >= 0 else area.right() - 220, self.settings.y if self.settings.y >= 0 else area.top() + 80)
        self.overlay.clamp_position()
        self.overlay.show()
        self.tray = QSystemTrayIcon(icon(), app)
        self.tray.setToolTip("Balatro AI Copilot · F9 分析 / F10 收起")
        menu = QMenu()
        menu.addAction("显示", self.reopen)
        menu.addAction("分析当前局面 · F9", self.analyze)
        menu.addAction("设置", self.open_settings)
        menu.addAction("自动打牌 · 当前盲注 · F11", self.toggle_auto)
        menu.addAction("停止自动 · F12", self.stop_auto)
        menu.addSeparator()
        menu.addAction("退出", self.quit)
        self.tray.setContextMenu(menu)
        self.tray_menu = menu
        self.tray.activated.connect(lambda reason: self.reopen() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
        self.hotkeys = Hotkeys(self.hotkey_analyze, self.hotkey_toggle, self.toggle_auto, self.stop_auto)
        app.installNativeEventFilter(self.hotkeys)
        self.hotkeys_ok = self.hotkeys.register()
        if not self.hotkeys_ok:
            self.overlay.message("F9 / F10 已被其他程序占用。关闭冲突程序后重启；也可使用浮窗按钮。")
        if not smoke:
            # Runtime never upgrades mod files. All changes use explicit setup.
            self.auto_transport.stop()  # Launch never resumes a prior run.
            self.configure_hud()
            if not self.store.key():
                QTimer.singleShot(150, self.open_settings)
            elif not self.settings.api_verified:
                QTimer.singleShot(150, self.verify)
        app.aboutToQuit.connect(self.cleanup)

    def configure_hud(self):
        if self.smoke:
            return
        try:
            if self.settings.in_game_hud:
                if not (GAME_DIR/'Mods/BalatroCopilotHUD/main.lua').is_file():
                    raise OSError('请退出游戏并运行检测游戏 / 修复安装。')
                self.settings.display_compatibility = False
                self.display_compatibility.enabled = False
                self.display_tick()
                self.store.save(self.settings)
                self.hud.enabled = True
                if self.hud.kind == "stopped":
                    self.hud.message("idle", "游戏内 AI 面板已连接。\n按 F9 分析，F10 展开/收起。")
                else:
                    self.hud.message(self.hud.kind, self.hud.text, self.hud.fingerprint)
                self.hud_timer.start()
                self.hud_tick()
                if not self.hud_attached:
                    self.overlay.message("游戏内面板已安装。请安全退出并重启 Balatro；加载后桌面浮窗自动隐藏。", True)
            else:
                if self.hud.enabled:
                    self.hud.message("stopped", "游戏内面板已关闭。")
                self.hud.enabled = False
                self.hud_timer.stop()
                self.hud_attached = False
                self.overlay.show()
        except (OSError, ValueError):
            log.exception("Game HUD installation unavailable")
            self.hud.enabled = False
            self.hud_timer.stop()
            self.overlay.message("游戏内面板安装失败，保留桌面浮窗。请查看本机日志。", True)

    def hud_tick(self):
        if not self.hud.enabled:
            return
        try:
            if time.monotonic()-self.hud_last_write >= 2:
                self.hud.flush()
                self.hud_last_write = time.monotonic()
            ready = self.hud.ready() is not None
            if ready:
                self.overlay.hide()
                if not self.hud.supports_blocks and not self.hud_upgrade_notice:
                    self.hud_upgrade_notice = True
                    self.tray.showMessage("新版模组等待重启", "请安全退出并重启 Balatro：分层排版和新增计分验算才能生效。当前仍兼容旧面板。")
            elif self.hud_attached:
                self.overlay.message("游戏内面板未连接，暂时使用桌面浮窗。请检查游戏是否运行。")
            self.hud_attached = ready
            self.hud_failures = 0
        except (OSError, ValueError):
            self.hud_failures += 1
            if self.hud_failures == 1:
                log.exception("Game HUD IPC temporarily unavailable; retrying")
            self.hud_attached = False
            if self.hud_failures == 3:
                self.overlay.message("游戏内面板暂未连接，使用桌面浮窗并继续重试。")

    def show_status(self, text, clear=False, kind="busy"):
        if self.pending_exit:
            return
        self.overlay.message(text, clear)
        if self.hud.enabled:
            try:
                self.hud.message(kind, text)
                self.hud_tick()
            except (OSError, ValueError):
                log.exception("HUD status could not be published")
                self.overlay.show()

    def hotkey_analyze(self):
        self.hotkey_analyses += 1
        self.analyze()

    def display_tick(self):
        try:
            self.display_compatibility.tick()
        except OSError:
            log.exception("Display compatibility local operation failed")
            self.display_timer.stop()
            self.overlay.message("显示兼容修复暂时失败；请关闭兼容选项后重试。")

    def hotkey_toggle(self):
        self.hotkey_toggles += 1
        if self.hud.enabled and self.hud.ready():
            try:
                self.hud.toggle()
            except OSError:
                log.exception("HUD toggle failed")
            return
        self.overlay.toggle()

    def save_position(self):
        self.settings.x, self.settings.y = self.overlay.x(), self.overlay.y()
        self.store.save(self.settings)

    def copy(self):
        if self.overlay.result_text:
            self.app.clipboard().setText(self.overlay.result_text)

    def open_settings(self):
        if self.auto_transport.active:
            self.stop_auto()
        if self.smoke:
            self.overlay.message("请在设置中填写 DeepSeek API Key。")
            return
        if self.dialog is not None:
            self.dialog.showNormal()
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        self.dialog = SettingsDialog(self.store, self.settings)
        self.dialog.finished.connect(self.settings_done)
        self.dialog.show()
        area = self.app.primaryScreen().availableGeometry()
        self.dialog.move(area.center() - self.dialog.rect().center())
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.dialog.key.setFocus()

    def reopen(self):
        log.info("Launch activation: restoring window")
        if self.hud.enabled and self.hud.ready():
            self.hud.message(self.hud.kind, self.hud.text, self.hud.fingerprint)
            if self.dialog is not None or not self.store.key():
                self.open_settings()
            return
        self.overlay.set_expanded(True)
        self.overlay.clamp_position()
        self.overlay.show()
        self.overlay.raise_()
        if self.dialog is not None or not self.store.key():
            self.open_settings()

    def settings_done(self, accepted):
        self.dialog.deleteLater()
        self.dialog = None
        if accepted:
            self.configure_hud()
            self.display_compatibility.enabled = self.settings.display_compatibility
            self.display_compatibility.failed.clear()
            self.display_tick()
        if accepted and not self.settings.api_verified:
            self.verify()

    def verify(self):
        if self.worker is not None or self.auto_worker is not None:
            self.show_status("正在处理上一请求，请稍候。")
            return
        self.start_worker(True)

    def analyze(self):
        if self.auto_worker is not None:
            self.overlay.message("自动运行中，请先按 F12 停止，再按 F9 手动分析。")
            if self.hud_attached:
                self.overlay.hide()
            return
        if self.worker is not None:
            # Keep the pending/result display; don't republish as a new request.
            self.overlay.message("正在分析上一请求，本次未重复调用 API。")
            if self.hud_attached:
                self.overlay.hide()
            return
        if not balatro_pids():
            self.show_status("未检测到 Balatro。", True, "error")
            return
        if self.smoke:
            self.overlay.message("正在读取局面…", True)
            self.worker = StateProbe()
            self.worker.result.connect(self.probe_result)
            self.worker.error.connect(lambda text: self.overlay.message(text, True))
            self.worker.finished.connect(self.finished)
            self.worker.start()
            return
        self.start_worker(False)

    def probe_result(self, data):
        self.smoke_refresh_result = data
        self.overlay.message(f"实时状态：{data['state']} · 刷新验证通过", True)

    def start_worker(self, verify):
        key = self.store.key()
        Redact.secrets.add(key)
        self.show_status("正在检查 API 连接…" if verify else "正在读取局面…", True)
        self.worker = Worker(key, self.settings, verify)
        self.worker.stage.connect(self.show_status)
        self.worker.result.connect(self.result)
        self.worker.error.connect(lambda text: self.show_status(text, True, "error"))
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def result(self, value):
        if self.pending_exit:
            return
        if value.get("auto_pending") and not self.auto_transport.active:
            return
        if value.get("verified"):
            self.settings.api_verified = True
            self.store.save(self.settings)
            self.show_status("连接成功。回到游戏按 F9 获取建议。", True, "idle")
        else:
            self.overlay.render(value["decision"], value["calculation"], value["state"], value["elapsed"])
            if self.hud.enabled:
                try:
                    self.hud.ready()
                    display = self.overlay.result_hud_text if self.hud.supports_blocks else self.overlay.result_text
                    self.hud.message("result", display, getattr(value["state"], "hud_fingerprint", ""))
                    self.hud_tick()
                except (OSError, ValueError):
                    log.exception("HUD recommendation could not be published")

    def finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        if self.pending_exit and self.auto_worker is None:
            self.app.quit()

    def toggle_auto(self):
        if self.auto_worker is not None:
            self.stop_auto()
            return
        if self.smoke:
            return
        if not self.settings.auto_play_enabled:
            self.show_status("自动模式默认关闭。请在设置中勾选允许实验自动打牌，再进入牌局按 F11。",True,"idle")
            self.open_settings()
            return
        if self.worker is not None or self.dialog is not None:
            self.show_status("请先完成当前分析或关闭设置，再启动自动。",False,"idle")
            return
        if 904 not in self.hotkeys.registered:
            self.show_status("F12 停止快捷键被占用，安全起见不能启动自动。请关闭冲突程序后重启助手。",True,"error")
            return
        key = self.store.key()
        if not key:
            self.open_settings()
            return
        Redact.secrets.add(key)
        try:
            self.auto_transport.start()
        except OSError:
            self.show_status("无法建立自动执行连接，未启动。",True,"error")
            return
        self.auto_timer.start()
        self.auto_worker=AutoWorker(key,self.settings,self.auto_transport)
        self.auto_worker.stage.connect(self.auto_status)
        self.auto_worker.recommendation.connect(self.result)
        self.auto_worker.stopped.connect(self.auto_stopped)
        self.auto_worker.finished.connect(self.auto_finished)
        self.show_status("实验自动已启动 · 当前盲注 · F12 / Escape 停止",True,"busy")
        self.auto_worker.start()

    def auto_status(self,text):
        if self.auto_transport.active:
            self.show_status(text,True,"busy")

    def auto_heartbeat(self):
        if not self.auto_transport.active:
            return
        try:
            self.auto_transport.heartbeat()
        except OSError:
            log.exception("Autoplay heartbeat failed")
            self.stop_auto()

    def stop_auto(self):
        was_active=self.auto_transport.active
        self.auto_timer.stop()
        revoked=True
        try:
            self.auto_transport.stop()
        except OSError:
            revoked=False
            log.exception("Autoplay lease revoke failed; expires within 3 seconds")
        if self.auto_worker is not None:
            self.auto_worker.requestInterruption()
        if was_active and not self.pending_exit:
            self.show_status("自动已停止；已提交的动作可能已执行，不能撤回。" if revoked else "已请求停止，通信异常；租约最多 3 秒失效。游戏内按 Escape 可立即取消。",True,"idle")

    def auto_stopped(self,text):
        was_active=self.auto_transport.active
        self.stop_auto()
        if was_active and not self.pending_exit:
            self.show_status(text,True,"idle")

    def auto_finished(self):
        worker,self.auto_worker=self.auto_worker,None
        if worker:
            worker.deleteLater()
        self.auto_timer.stop()
        if self.pending_exit and self.worker is None:
            self.app.quit()

    def quit(self):
        self.stop_auto()
        self.save_position()
        self.hud_timer.stop()
        if self.hud.enabled:
            try:
                self.hud.message("stopped", "助手已退出。")
            except OSError:
                pass
            self.hud.enabled = False
        self.display_timer.stop()
        self.display_compatibility.enabled = False
        self.display_tick()
        if self.worker is not None or self.auto_worker is not None:
            self.pending_exit = True
            if self.worker is not None:
                self.worker.requestInterruption()
            self.overlay.hide()
            self.tray.hide()
        else:
            self.app.quit()

    def cleanup(self):
        self.stop_auto()
        self.hud_timer.stop()
        if self.hud.enabled:
            try:
                self.hud.message("stopped", "助手已退出。")
            except OSError:
                pass
            self.hud.enabled = False
        self.display_timer.stop()
        self.display_compatibility.enabled = False
        self.display_tick()
        self.hotkeys.close()
        self.tray.hide()
        log.info("App exit")


def emit_test_key(vk: int):
    inputs = (Input * 2)()
    for i in range(2):
        inputs[i].type = 1
        inputs[i].ki = KeyboardInput(vk, 0, 0 if i == 0 else 2, 0, 0)
    user32.SendInput(2, inputs, ctypes.sizeof(Input))


def run_smoke(controller: Controller, report: Path):
    # Exercises real WM_HOTKEY handling without API credentials or mock data.
    app = controller.app
    QTimer.singleShot(250, lambda: emit_test_key(0x79))
    QTimer.singleShot(500, lambda: emit_test_key(0x79))
    QTimer.singleShot(750, lambda: emit_test_key(0x78))

    def finish():
        report.parent.mkdir(parents=True, exist_ok=True)
        controller.overlay.grab().save(str(report.with_suffix(".png")))
        hwnd = int(controller.overlay.winId())
        user32.GetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        exstyle = user32.GetWindowLongPtrW(hwnd, -20)
        data = {"started": True, "frozen": bool(getattr(sys, "frozen", False)), "hotkeys_registered": controller.hotkeys_ok, "f10_received": controller.hotkey_toggles, "f9_received": controller.hotkey_analyses, "topmost": bool(exstyle & 8), "no_activate": bool(exstyle & 0x08000000), "frameless": bool(controller.overlay.windowFlags() & Qt.WindowType.FramelessWindowHint), "tray_visible": controller.tray.isVisible(), "status": controller.overlay.status.text(), "width": controller.overlay.width(), "height": controller.overlay.height(), "live_refresh": controller.smoke_refresh_result}
        report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        controller.quit()

    QTimer.singleShot(1400, finish)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", type=Path)
    parser.add_argument("--trainer", action="store_true", help="Open opt-in isolated full-run trainer")
    parser.add_argument("--quit", action="store_true", help="Gracefully exit an existing instance and restore its display adjustment")
    parser.add_argument('--setup',action='store_true',help='Install or repair game integration')
    parser.add_argument('--setup-preview',type=Path)
    parser.add_argument('--uninstall-integration',action='store_true')
    parser.add_argument('--package-check',type=Path,help='Offline bundle validation; no game or API actions')
    args = parser.parse_args()
    if args.quit:
        deadline=time.monotonic()+10
        while True:
            active=False
            for name in ('BalatroAICopilot','BalatroAICopilotTrainer','BalatroAICopilotSetup'):
                other=SingleInstance(name)
                try:
                    if other.existing:active=True;other.request_quit()
                finally:other.close()
            if not active:return 0
            if time.monotonic()>deadline:return 1
            time.sleep(.1)
    if args.package_check:
        from core.installation import verified_archive,archive_files,exporter_text
        for name in ('lovely','steamodded'):
            path,_=verified_archive(resource('dependencies'),name);archive_files(path)
        assert '-- BALATRO_COPILOT_SHOP_SNAPSHOT_V1' in exporter_text(resource('assets'))
        args.package_check.parent.mkdir(parents=True,exist_ok=True)
        args.package_check.write_text(json.dumps({'bundle_verified':True,'frozen':bool(getattr(sys,'frozen',False)),'network_requests':0,'game_writes':0}),encoding='utf-8')
        return 0
    if args.uninstall_integration:
        from core.installation import uninstall_default
        try:
            result=uninstall_default()
            if result['preserved']:return 2
            from config.settings import set_startup
            set_startup(False)
            return 0
        except Exception:
            return 1
    if args.setup or args.setup_preview:
        from app.setup_window import setup_main
        return setup_main(args.setup_preview)
    if args.trainer or Path(sys.executable).stem.lower() == 'balatroautotrainer':
        from app.trainer_window import trainer_main
        return trainer_main()
    instance = SingleInstance("BalatroAICopilot" + ("Smoke" if args.smoke_test else ""))
    if instance.existing:
        if args.quit:
            instance.request_quit()
        else:
            instance.request_open()
        instance.close()
        return 0
    if args.quit:
        instance.close()
        return 0
    if not args.smoke_test and not (GAME_DIR/'Mods/BalatroStateExporter/main.lua').is_file():
        instance.close()
        from app.setup_window import setup_main
        return setup_main()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Balatro AI Copilot")
    app.setQuitOnLastWindowClosed(False)
    controller = Controller(app, args.smoke_test)
    activation_timer = QTimer(app)
    def process_launch_request():
        if instance.quit_requested():
            controller.quit()
        elif instance.requested():
            controller.reopen()
    activation_timer.timeout.connect(process_launch_request)
    activation_timer.start(100)
    if args.smoke_test:
        run_smoke(controller, args.smoke_test)
    result = app.exec()
    instance.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
