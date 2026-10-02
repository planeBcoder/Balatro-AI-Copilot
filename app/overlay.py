from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea, QSizePolicy

from core.probability import NAMES
from core.presentation import card_label, format_candidate, probability_label, metric, document, shop_document
from core.windows import no_activate, pin_topmost, foreground_game_window, window_above, user32

SUITS = {"S": "♠", "H": "♥", "C": "♣", "D": "♦"}
HAND_NAMES = {**NAMES, "high_card": "高牌", "five_kind": "五条"}


STYLE = """
QWidget { color: #e8edf7; font-family: 'Microsoft YaHei UI'; font-size: 12px; }
QPushButton { border: none; border-radius: 5px; padding: 4px 7px; background: #253044; color: #c6d2e7; }
QPushButton:hover { background: #34445e; }
QLabel { background: transparent; }
QFrame#option { background: #1b2432; border: 1px solid #304057; border-radius: 9px; }
QFrame#best { background: #192e31; border: 1px solid #5fbcb0; border-radius: 9px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #18202d; width: 5px; }
QScrollBar::handle:vertical { background: #526075; border-radius: 2px; min-height: 20px; }
"""


class DragHeader(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.anchor = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.anchor = event.globalPosition().toPoint() - self.window().pos()

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            self.window().move(event.globalPosition().toPoint() - self.anchor)

    def mouseReleaseEvent(self, event):
        self.anchor = None
        self.window().position_changed.emit()


class Overlay(QWidget):
    analyze_requested = Signal()
    settings_requested = Signal()
    copy_requested = Signal()
    position_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Balatro AI Copilot")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(STYLE)
        self.expanded = False
        self.result_text = ""
        self.result_hud_text = ""
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(12, 9, 12, 12)
        self.outer.setSpacing(8)
        self.header = DragHeader(self)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self.title = QLabel("🃏 AI")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.title.setStyleSheet("font-weight: 700; color: #85d9cb; font-size: 13px;")
        row.addWidget(self.title, 1)
        self.toggle_button = QPushButton("▾")
        self.toggle_button.setToolTip("展开 / 收起 · F10")
        self.toggle_button.clicked.connect(self.toggle)
        self.settings_button = QPushButton("⚙")
        self.settings_button.setToolTip("设置")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.close_button = QPushButton("×")
        self.close_button.setToolTip("收至托盘")
        self.close_button.clicked.connect(self.hide)
        for button in (self.toggle_button, self.settings_button, self.close_button):
            button.setFixedSize(26, 25)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(button)
        self.outer.addWidget(self.header)
        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        self.status = QLabel("游戏中按 F9 获取建议")
        self.status.setWordWrap(True)
        body_layout.addWidget(self.status)
        self.context = QLabel("")
        self.context.setWordWrap(True)
        self.context.setStyleSheet("color: #8999af; font-size: 11px;")
        body_layout.addWidget(self.context)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMaximumHeight(420)
        self.options = QWidget()
        self.options_layout = QVBoxLayout(self.options)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(7)
        self.scroll.setWidget(self.options)
        body_layout.addWidget(self.scroll)
        self.footer = QLabel("")
        self.footer.setWordWrap(True)
        self.footer.setStyleSheet("color: #a4b0c3; font-size: 11px;")
        body_layout.addWidget(self.footer)
        buttons = QHBoxLayout()
        self.analyze_button = QPushButton("分析 · F9")
        self.analyze_button.clicked.connect(self.analyze_requested.emit)
        self.copy_button = QPushButton("复制结果")
        self.copy_button.clicked.connect(self.copy_requested.emit)
        for button in (self.analyze_button, self.copy_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        buttons.addWidget(self.analyze_button)
        buttons.addWidget(self.copy_button)
        body_layout.addLayout(buttons)
        self.outer.addWidget(self.body)
        self.set_expanded(False)
        self.topmost_guard = QTimer(self)
        self.topmost_guard.setInterval(250)
        self.topmost_guard.timeout.connect(self.maintain_topmost)
        self.topmost_guard.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect().adjusted(1, 1, -1, -1)), 13, 13)
        painter.fillPath(path, QColor("#131c29"))
        painter.setPen(QPen(QColor("#334257"), 1))
        painter.drawPath(path)

    def showEvent(self, event):
        super().showEvent(event)
        no_activate(int(self.winId()))
        pin_topmost(int(self.winId()))

    def maintain_topmost(self):
        # Respect explicit hiding; don't compete with Settings or unrelated apps.
        if not self.isVisible():
            return
        game = foreground_game_window()
        if not game:
            return
        hwnd = int(self.winId())
        if not user32.GetWindowLongPtrW(hwnd, -20) & 8 or not window_above(hwnd, game):
            no_activate(hwnd)
            pin_topmost(hwnd)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def set_expanded(self, expanded: bool):
        self.expanded = expanded
        self.body.setVisible(expanded)
        self.title.setText("🃏 Balatro AI Copilot" if expanded else "🃏 AI")
        self.toggle_button.setText("▴" if expanded else "▾")
        self.setFixedWidth(380 if expanded else 196)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.adjustSize()
        if not expanded:
            self.setFixedHeight(49)
        self.clamp_position()

    def clamp_position(self):
        screen = self.screen()
        if screen:
            area = screen.availableGeometry()
            self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)), max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))

    def toggle(self):
        self.set_expanded(not self.expanded)
        self.show()

    def clear_options(self):
        while self.options_layout.count():
            item = self.options_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def message(self, text: str, clear: bool = False):
        if clear:
            self.clear_options()
            self.context.clear()
            self.footer.clear()
            self.result_text = ""
            self.result_hud_text = ""
            self.scroll.hide()
        self.status.setText(text)
        self.set_expanded(True)
        self.show()

    def render(self, decision, calculation: dict, state, elapsed: float):
        if calculation.get('kind')=='shop':
            return self.render_shop(decision,calculation,state,elapsed)
        self.clear_options()
        self.scroll.show()
        self.status.setText("已找到直接过关打法" if calculation.get("immediate_finish_ids") else "当前建议")
        self.context.setText(f"Ante {state.ante or '?'} · {state.blind.name or '?'} · {state.score}/{state.blind.target or '?'}\n出牌 {state.hands_left} · 弃牌 {state.discards_left} · 读取于 {state.exported_at[11:19]} UTC")
        lookup = {c["id"]: c for c in calculation["candidates"]}
        texts = ["Balatro AI Copilot", "当前可选操作："]
        for rec in decision.recommendations:
            c = lookup[rec.candidate_id]
            frame = QFrame()
            frame.setObjectName("best" if rec.rank == 1 else "option")
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(10, 9, 10, 9)
            layout.setSpacing(5)
            action = ("推荐 · " if rec.rank == 1 else "备选 · ") + format_candidate(c)
            label = QLabel(action)
            label.setWordWrap(True)
            label.setStyleSheet("font-weight: 700; font-size: 17px; color: #e5fff8;" if rec.rank == 1 else "font-weight: 600; font-size: 14px;")
            layout.addWidget(label)
            texts.append(action)
            p_text = metric(c, calculation)
            if p_text:
                p_label = QLabel(p_text)
                p_label.setWordWrap(True)
                p_label.setStyleSheet("color: #85d9cb; font-size: 13px;")
                layout.addWidget(p_label)
                texts.append(p_text)
            if c["action"] == "discard":
                keep = "保留：" + "、".join(card_label(state.hand[i].code) for i in c["keep_indices"])
                keep_label = QLabel(keep)
                keep_label.setWordWrap(True)
                keep_label.setStyleSheet("color: #afbdd1; font-size: 11px;")
                layout.addWidget(keep_label)
                texts.append(keep)
            reason = QLabel(rec.reason)
            reason.setWordWrap(True)
            layout.addWidget(reason)
            texts.append(rec.reason)
            self.options_layout.addWidget(frame)
        limits = calculation.get("score_limitations") or []
        self.footer.setText(f"{decision.summary}\n" + ("验算范围："+"；".join(limits[:2])+"\n" if limits else "") + f"耗时 {elapsed:.1f} 秒 · 补牌概率不是整轮胜率")
        texts.append(self.footer.text())
        _, self.result_text, self.result_hud_text = document(decision, calculation, state, elapsed)
        self.set_expanded(True)
        self.show()

    def render_shop(self,decision,calculation,state,elapsed):
        self.clear_options()
        self.scroll.show()
        self.status.setText('商店建议 · 只分析，不代操作')
        blocks,self.result_text,self.result_hud_text=shop_document(decision,calculation,state,elapsed)
        context_count=next(i for i,b in enumerate(blocks) if b[0] in {'A','B'})
        self.context.setText('\n'.join(text for _,text in blocks[:context_count]))
        current=None
        styles={'A':'font-weight:700;font-size:17px;color:#e5fff8;',
            'B':'font-weight:600;font-size:14px;', 'P':'font-size:13px;color:#85d9cb;',
            'K':'font-size:11px;color:#afbdd1;', 'R':'font-size:13px;'}
        footer=[]
        for tag,text in blocks[context_count:]:
            if tag in {'S','F'}:footer.append(text);continue
            if tag in {'A','B'}:
                frame=QFrame();frame.setObjectName('best' if tag=='A' else 'option')
                current=QVBoxLayout(frame);current.setContentsMargins(10,9,10,9);current.setSpacing(6)
                self.options_layout.addWidget(frame)
            label=QLabel(text);label.setWordWrap(True);label.setStyleSheet(styles[tag])
            current.addWidget(label)
        self.footer.setText('\n'.join(footer))
        self.set_expanded(True)
        self.show()
