from ctypes import wintypes as w

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from app.overlay import Overlay
from core.windows import user32, no_activate, pin_topmost, window_above, foreground_game_window

_app = None


def qt():
    global _app
    _app = QApplication.instance() or QApplication([])
    return _app


def test_recover_above_another_topmost_window_without_activating(monkeypatch):
    qt()
    overlay = Overlay()
    blocker = QWidget()
    blocker.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    blocker.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    blocker.resize(240, 120)
    overlay.show()
    blocker.show()
    qt().processEvents()
    hwnd, other = int(overlay.winId()), int(blocker.winId())
    no_activate(other)
    assert pin_topmost(other)
    assert not window_above(hwnd, other)
    foreground = user32.GetForegroundWindow()
    monkeypatch.setattr("app.overlay.foreground_game_window", lambda: other)
    QTest.qWait(350)
    assert window_above(hwnd, other)
    assert user32.GetForegroundWindow() == foreground
    # Also recover after losing the topmost band entirely.
    assert user32.SetWindowPos(hwnd, w.HWND(-2), 0, 0, 0, 0, 0x13)
    assert not user32.GetWindowLongPtrW(hwnd, -20) & 8
    overlay.maintain_topmost()
    assert user32.GetWindowLongPtrW(hwnd, -20) & 8
    assert window_above(hwnd, other)
    assert user32.GetForegroundWindow() == foreground
    overlay.hide()
    blocker.hide()
    overlay.deleteLater()
    blocker.deleteLater()


def test_hidden_overlay_stays_hidden_and_skips_game_lookup(monkeypatch):
    qt()
    overlay = Overlay()
    monkeypatch.setattr("app.overlay.foreground_game_window", lambda: (_ for _ in ()).throw(AssertionError("Hidden overlay must not poll")))
    overlay.maintain_topmost()
    assert not overlay.isVisible()
    overlay.deleteLater()


def test_no_repin_over_unrelated_apps(monkeypatch):
    qt()
    overlay = Overlay()
    overlay.show()
    qt().processEvents()
    monkeypatch.setattr("app.overlay.foreground_game_window", lambda: None)
    monkeypatch.setattr("app.overlay.pin_topmost", lambda _: (_ for _ in ()).throw(AssertionError("Do not fight other apps")))
    overlay.maintain_topmost()
    overlay.hide()
    overlay.deleteLater()


def test_foreground_lookup_returns_an_existing_game_or_none():
    result = foreground_game_window()
    assert result is None or isinstance(result, int)
