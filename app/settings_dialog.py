from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QComboBox, QPushButton, QCheckBox, QSpinBox
from PySide6.QtCore import Qt

from config.settings import SettingsStore, Settings, set_startup


class SettingsDialog(QDialog):
    def __init__(self, store: SettingsStore, settings: Settings):
        super().__init__()
        self.store, self.settings = store, settings
        self.setWindowTitle("Balatro AI Copilot · 设置")
        # Configuration accepts focus; only the in-game overlay is non-activating.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedWidth(360)
        self.setStyleSheet("QDialog { background: #172130; } QLabel,QCheckBox { color:#dde6f4; } QLineEdit,QComboBox,QSpinBox { background:#26354a; color:#eef4ff; border:1px solid #465773; border-radius:5px; padding:7px; } QPushButton { background:#5ac3b0; color:#112b2b; border:0; border-radius:5px; padding:10px; font-weight:bold; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(QLabel("DeepSeek API Key"))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("输入一次，以后自动记住")
        self.key.setText(store.key())
        layout.addWidget(self.key)
        privacy = QLabel("密钥由 Windows 加密保存在本机。\n按 F9 或手动启动自动模式后会发送状态，可能产生 API 费用。")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        layout.addWidget(QLabel("模型"))
        self.model = QComboBox()
        self.model.addItems(["deepseek-flash", "deepseek-v4-pro"])
        self.model.setCurrentText(settings.model)
        layout.addWidget(self.model)
        self.samples = QSpinBox()
        self.samples.setRange(500, 20000)
        self.samples.setSingleStep(500)
        self.samples.setValue(settings.simulation_count)
        layout.addWidget(QLabel("模拟次数（仅复杂补牌事件）"))
        layout.addWidget(self.samples)
        self.startup = QCheckBox("随 Windows 启动")
        self.startup.setChecked(settings.startup)
        layout.addWidget(self.startup)
        self.in_game_hud = QCheckBox("游戏内 AI 面板（首次启用需重启游戏）")
        self.in_game_hud.setChecked(settings.in_game_hud)
        self.in_game_hud.setToolTip("只绘制建议；不改玩法或存档。启用后停用 1 像素兼容修复，API 设置仍在本程序。")
        layout.addWidget(self.in_game_hud)
        self.auto_play = QCheckBox("允许实验自动打牌 · F11 启停 / F12 停止")
        self.auto_play.setChecked(settings.auto_play_enabled)
        self.auto_play.setToolTip("只自动处理当前盲注的出牌/弃牌。遇到商店、结算、未知效果会停止；游戏内 Escape 也可取消。启用后需要重启游戏载入独立执行模组。")
        layout.addWidget(self.auto_play)
        self.display_compatibility = QCheckBox("无边框显示兼容（顶部留白 1 像素）")
        self.display_compatibility.setChecked(settings.display_compatibility)
        self.display_compatibility.setToolTip("游戏高度减 1 像素并下移 1 像素，底部仍覆盖任务栏；取消勾选或退出副驾驶会尝试恢复。不改任务栏设置、游戏代码或存档。")
        layout.addWidget(self.display_compatibility)
        self.in_game_hud.toggled.connect(lambda enabled: self.display_compatibility.setEnabled(not enabled))
        self.display_compatibility.setEnabled(not settings.in_game_hud)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        repair=QPushButton('检测游戏 / 修复安装')
        repair.clicked.connect(self.open_setup)
        layout.addWidget(repair)
        save = QPushButton("保存并开始")
        save.clicked.connect(self.save)
        layout.addWidget(save)

    def open_setup(self):
        import subprocess,sys
        from config.settings import resource
        command=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(resource('main.py'))]
        subprocess.Popen(command+['--setup'],creationflags=subprocess.CREATE_NO_WINDOW)
        self.reject()

    def save(self):
        key = self.key.text().strip()
        if not key or len(key) < 8:
            self.error.setText("请填写有效的 DeepSeek API Key。")
            return
        try:
            key_changed = key != self.store.key()
            changed = key_changed or self.model.currentText() != self.settings.model
            if key_changed:
                self.store.save_key(key)
            if changed:
                self.settings.api_verified = False
            if self.startup.isChecked() != self.settings.startup:
                set_startup(self.startup.isChecked())
            self.settings.model = self.model.currentText()
            self.settings.simulation_count = self.samples.value()
            self.settings.startup = self.startup.isChecked()
            self.settings.display_compatibility = self.display_compatibility.isChecked()
            self.settings.in_game_hud = self.in_game_hud.isChecked()
            self.settings.auto_play_enabled = self.auto_play.isChecked()
            if self.settings.in_game_hud:
                self.settings.display_compatibility = False
            self.store.save(self.settings)
            self.accept()
        except OSError:
            self.error.setText("设置保存失败，请检查本机文件权限。")
