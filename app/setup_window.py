"""First-run and repair workflow. No network or game launch during setup."""
from pathlib import Path
import time
from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog, QComboBox

from config.settings import SettingsStore
from core.game_detection import discover_games, inspect_game
from core.installation import default_integration
from core.single_instance import SingleInstance


class InstallWorker(QThread):
    result=Signal(object)
    error=Signal(str)
    def __init__(self,game,store):
        super().__init__();self.game=game;self.store=store
    def run(self):
        try:
            # Existing assistant must release its executor lease and stop writing mods.
            deadline=time.monotonic()+10
            while True:
                instance=SingleInstance()
                existing=instance.existing
                if existing:instance.request_quit()
                instance.close()
                if not existing:break
                if time.monotonic()>deadline:raise OSError('助手仍在结束当前请求，请稍后重试。')
                time.sleep(.1)
            settings=self.store.load()
            previous=settings.game_install
            if previous and Path(previous).resolve()!=self.game.resolve():
                # A tracked integration cannot silently migrate to a second game.
                if (self.store.root/'integration.json').exists():raise OSError('请先卸载旧游戏的适配组件，再切换游戏目录。')
            settings.game_install=str(self.game)
            self.store.save(settings)  # Recovery location exists before any game write.
            result=default_integration(self.game).install()
            settings.in_game_hud=True
            settings.display_compatibility=False
            # Do not enable auto-play or replace the user's key/preferences.
            self.store.save(settings)
            self.result.emit(result)
        except Exception as exc:self.error.emit(str(exc))


class SetupWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.store=SettingsStore();self.worker=None
        self.setWindowTitle('Balatro AI Copilot · 安装与修复')
        self.setMinimumWidth(640)
        self.setStyleSheet('QWidget {background:#14212a;color:#edf5f4;font:14px "Microsoft YaHei UI";} QLabel#title {font-size:26px;font-weight:700;} QLabel#hint {color:#a7babd;} QLineEdit,QComboBox {background:#22353f;border:1px solid #425660;border-radius:6px;padding:10px;} QPushButton {background:#28424c;border:0;border-radius:6px;padding:11px;} QPushButton#primary {background:#7bd9c8;color:#102b2a;font-weight:700;} QPushButton:disabled {color:#7d9298;background:#263940;}')
        layout=QVBoxLayout(self);layout.setContentsMargins(28,26,28,26);layout.setSpacing(16)
        title=QLabel('把 AI 副驾驶接到你的 Balatro');title.setObjectName('title');layout.addWidget(title)
        hint=QLabel('Windows 10/11 · 64 位 · Steam 1.0.1o-FULL\n安装离线适配组件，不修改游戏本体或存档。');hint.setObjectName('hint');layout.addWidget(hint)
        self.games=QComboBox();self.games.currentIndexChanged.connect(self.choose);layout.addWidget(self.games)
        line=QHBoxLayout();self.path=QLineEdit();self.path.setPlaceholderText('包含 Balatro.exe 的游戏目录');line.addWidget(self.path)
        browse=QPushButton('选择目录');browse.clicked.connect(self.browse);line.addWidget(browse);layout.addLayout(line)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        warning=QLabel('1. 先退出游戏，再安装或修复。\n2. 已有兼容加载器会保留；不兼容组件不会被覆盖。\n3. 自动游戏默认关闭；AI 密钥需要你自己填写。');warning.setObjectName('hint');warning.setWordWrap(True);layout.addWidget(warning)
        self.install=QPushButton('安装并适配 / 修复');self.install.setObjectName('primary');self.install.clicked.connect(self.start);layout.addWidget(self.install)
        self.open_app=QPushButton('完成，打开 AI 副驾驶');self.open_app.setEnabled(False);self.open_app.clicked.connect(self.launch);layout.addWidget(self.open_app)
        games=discover_games(saved_path=self.store.load().game_install)
        self.games.addItem('自动检测到的游戏（也可以手动选择）',None)
        for game in games:self.games.addItem(f'{game.version}  ·  {game.path}',str(game.path))
        if len(games)==1:self.games.setCurrentIndex(1)
        else:self.status.setText('请选择游戏目录。未检测到时，可在 Steam → 管理 → 浏览本地文件中找到。')

    def choose(self,index):
        path=self.games.itemData(index)
        if path:self.path.setText(path);self.check()
    def browse(self):
        path=QFileDialog.getExistingDirectory(self,'选择包含 Balatro.exe 的目录',self.path.text())
        if path:self.path.setText(path);self.check()
    def check(self):
        game=inspect_game(self.path.text())
        self.status.setText('已检测：'+game.version+'。请退出游戏后安装。' if game.supported else game.reason)
        return game
    def start(self):
        if self.worker:return
        game=self.check()
        if not game.supported:return
        self.install.setEnabled(False);self.open_app.setEnabled(False)
        self.status.setText('正在检查组件、校验文件并安装…')
        self.worker=InstallWorker(game.path,self.store)
        self.worker.result.connect(self.success);self.worker.error.connect(self.failure)
        self.worker.finished.connect(self.finished);self.worker.start()
    def success(self,result):
        self.status.setText(f'适配完成（更新 {result["changed"]} 个文件）。\n接下来打开游戏和副驾驶，在设置填写你自己的 DeepSeek API Key。\n选牌或商店时按 F9 获取建议，F10 显示 / 隐藏面板。')
        self.open_app.setEnabled(True)
    def failure(self,error):self.status.setText('未完成：'+error)
    def finished(self):
        self.install.setEnabled(True)
        self.worker.deleteLater();self.worker=None
    def launch(self):
        from config.settings import resource
        import subprocess,sys
        command=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(resource('main.py'))]
        subprocess.Popen(command,creationflags=subprocess.CREATE_NO_WINDOW)
        self.close()
    def closeEvent(self,event):
        if self.worker:
            self.status.setText('正在完成文件事务，请稍等再关闭。');event.ignore()
        else:event.accept()


def setup_main(preview=None):
    instance=SingleInstance('BalatroAICopilotSetup'+('Preview' if preview else ''))
    if instance.existing:
        instance.request_open();instance.close();return 0
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(True)
    window=SetupWindow();window.show()
    timer=QTimer(window)
    def requests():
        if instance.quit_requested():window.close()
        elif instance.requested():window.showNormal();window.raise_();window.activateWindow()
    timer.timeout.connect(requests);timer.start(100)
    if preview:
        def capture():
            preview.parent.mkdir(parents=True,exist_ok=True)
            window.grab().save(str(preview));window.close()
        QTimer.singleShot(300,capture)
    result=app.exec();instance.close();return result
