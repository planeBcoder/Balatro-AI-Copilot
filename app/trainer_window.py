"""Explicit start/stop UI for isolated, full-run real-game training."""
from pathlib import Path
import time
from PySide6.QtCore import QThread, Signal, QTimer, QProcess
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from ai.deepseek_client import DeepSeekClient
from config.settings import SettingsStore, APP_DIR, resource
from core.fullrun import FullRunTrainer
from core.lab_runtime import build_lab, LAB_PROFILE
from core.single_instance import SingleInstance
from core.state_refresh import StateRefresher
from core.windows import process_pids


class TrainingWorker(QThread):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.trainer = None

    def run(self):
        try:
            runtime = APP_DIR / 'trainer-runtime'
            output = APP_DIR / 'fullrun-history' / time.strftime('%Y%m%d-%H%M%S')
            self.progress.emit('准备独立游戏副本…')
            if not process_pids('BalatroCopilotLab.exe'):
                exe = build_lab(runtime, output / 'source-integrity.json')
                if self.isInterruptionRequested():
                    return
                ok, pid = QProcess.startDetached(str(exe), [], str(runtime))
                if not ok:
                    raise RuntimeError('独立游戏副本启动失败。')
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if self.isInterruptionRequested():
                        return
                    if (LAB_PROFILE / 'balatro_copilot_ready.txt').exists() and process_pids('BalatroCopilotLab.exe'):
                        break
                    time.sleep(.2)
            store = SettingsStore()
            settings = store.load()
            client = DeepSeekClient(store.key(), settings.model, resource('prompts/decision_system.md'))
            refresh = StateRefresher(LAB_PROFILE, lambda: process_pids('BalatroCopilotLab.exe'), lambda _: None)
            trainer = FullRunTrainer(LAB_PROFILE, refresh.refresh, client, output)
            self.trainer = trainer
            if self.isInterruptionRequested():
                trainer.stop.set()
            original = trainer.log
            def log(event, **kw):
                original(event, **kw)
                if event == 'decision':
                    action = '出牌' if kw['action'] == 'play' else '弃牌'
                    self.progress.emit(f'第 {trainer.runs} 次尝试 · 底注 {kw["ante"]}\n{kw["blind"]} · {action} {" ".join(kw["cards"])}')
                elif event == 'loss':
                    self.progress.emit(f'本局结束，正常重新开局 · 最高底注 {trainer.max_ante}')
                elif event == 'game_action':
                    self.progress.emit(f'第 {trainer.runs} 次尝试 · 底注 {kw["ante"]}\n处理商店 / 盲注 / 结算…')
            trainer.log = log
            result = trainer.run()
            if result:
                self.completed.emit({**result, 'output': str(output)})
            else:
                self.failed.emit('已停止，游戏进度保留在独立存档中。')
        except Exception as exc:
            self.failed.emit('已安全停止：' + str(exc))

    def cancel(self):
        self.requestInterruption()
        if self.trainer:
            self.trainer.stop.set()
            self.trainer.transport.stop()


class TrainerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.closing = False
        self.setWindowTitle('Balatro AI · 自动闯关')
        self.resize(510, 300)
        layout = QVBoxLayout(self)
        title = QLabel('自动闯关 + AI 辅助')
        title.setStyleSheet('font-size:22px;font-weight:600')
        layout.addWidget(title)
        info = QLabel('使用独立存档，按游戏正常规则自动出牌、弃牌、购物与重开。\n不修改原存档、分数、牌堆或随机数。白注，通关后停止。\nAI 使用已保存的密钥，最多 120 次决策；可能消耗余额。\n最多 40 局 / 4000 个动作，不保证每局通关。')
        info.setWordWrap(True)
        layout.addWidget(info)
        self.status = QLabel('点击开始才会运行。原版游戏可照常使用。')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('font-size:16px;color:#235649;padding:12px')
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.start_button = QPushButton('开始 / 继续闯关')
        self.stop_button = QPushButton('停止')
        self.stop_button.setEnabled(False)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)

    def start(self):
        if self.worker:
            return
        self.worker = TrainingWorker()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.worker.progress.connect(self.status.setText)
        self.worker.failed.connect(self.status.setText)
        self.worker.completed.connect(lambda r: self.status.setText(f'已真实通关！\n{r["boss"]}：{r["score"]} / {r["target"]}\n证据：{r["output"]}'))
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def stop(self):
        if self.worker:
            self.status.setText('正在停止，已提交的动作不能撤回…')
            self.worker.cancel()

    def finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if self.closing:
            self.close()

    def closeEvent(self, event):
        if self.worker:
            self.closing = True
            self.stop()
            event.ignore()
        else:
            event.accept()


def trainer_main():
    instance = SingleInstance('BalatroAICopilotTrainer')
    if instance.existing:
        instance.request_open()
        instance.close()
        return 0
    app = QApplication.instance() or QApplication([])
    window = TrainerWindow()
    window.show()
    timer = QTimer(window)
    def activate():
        if instance.quit_requested():
            window.close()
        elif instance.requested():
            window.showNormal()
            window.raise_()
            window.activateWindow()
    timer.timeout.connect(activate)
    timer.start(200)
    result = app.exec()
    instance.close()
    return result
