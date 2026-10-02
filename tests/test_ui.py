import json
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from ai.schemas import Decision
from app.overlay import Overlay
from app.settings_dialog import SettingsDialog
from config.settings import SettingsStore, Settings
from core.calculation import candidates
from core.state_schema import parse_state
from main import Controller, Worker

_app = None


def qt():
    global _app
    _app = QApplication.instance() or QApplication([])
    return _app


def test_ui_recommendations_are_separate_and_do_not_invent_win_rate(state_data):
    qt()
    state = parse_state(json.dumps(state_data))
    calc = candidates(state, 500)
    chosen = calc["candidates"][:3]
    decision = Decision.model_validate({"recommendations": [{"rank": i + 1, "candidate_id": c["id"], "action": c["action"], "cards": c["cards"], "win_probability": None, "probability_method": "unavailable", "reason": "保留后续补牌机会；当前分数有限。"} for i, c in enumerate(chosen)], "recommended_rank": 1, "summary": "优先采用方案一。"})
    overlay = Overlay()
    overlay.show()
    overlay.render(decision, calc, state, 1.23)
    qt().processEvents()
    assert overlay.options_layout.count() == 3
    assert overlay.expanded and overlay.width() == 380
    assert "暂无法精确计算" not in overlay.result_text
    assert overlay.result_hud_text.startswith("BACP_BLOCKS_V1\nM\t")
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    overlay.grab().save(str(Path(__file__).resolve().parents[3] / "work" / "result-preview.png"))
    overlay.toggle()
    assert overlay.height() == 49 and not overlay.expanded
    overlay.hide()
    overlay.deleteLater()


def test_first_run_settings_mask_key_and_dpapi(tmp_path):
    qt()
    store = SettingsStore(tmp_path)
    dialog = SettingsDialog(store, Settings())
    assert dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert not dialog.display_compatibility.isChecked()
    assert dialog.key.echoMode() == dialog.key.EchoMode.Password
    dialog.key.setText("unit-test-only-value")
    dialog.display_compatibility.setChecked(True)
    dialog.save()
    assert store.key() == "unit-test-only-value"
    assert store.load().display_compatibility
    dialog.deleteLater()


def test_reopen_restores_hidden_overlay_and_requests_missing_key(tmp_path, monkeypatch):
    qt()
    controller = Controller(qt(), tmp_path / "smoke.json")
    controller.overlay.hide()
    opened = []
    monkeypatch.setattr(controller, "open_settings", lambda: opened.append(True))
    controller.reopen()
    qt().processEvents()
    assert controller.overlay.isVisible() and controller.overlay.expanded
    assert opened == [True]
    controller.cleanup()
    controller.overlay.hide()
    controller.overlay.deleteLater()


def test_settings_mouse_click_opens_and_restores_actual_dialog(tmp_path):
    qt()
    controller = Controller(qt(), tmp_path / "smoke.json")
    # Use an isolated empty profile, but exercise the normal settings path.
    controller.smoke = None
    qt().processEvents()
    QTest.mouseClick(controller.overlay.settings_button, Qt.MouseButton.LeftButton)
    qt().processEvents()
    assert controller.dialog is not None and controller.dialog.isVisible()
    original = controller.dialog
    original.hide()
    QTest.mouseClick(controller.overlay.settings_button, Qt.MouseButton.LeftButton)
    qt().processEvents()
    assert controller.dialog is original and original.isVisible()
    original.reject()
    qt().processEvents()
    assert controller.dialog is None
    controller.cleanup()
    controller.overlay.hide()
    controller.overlay.deleteLater()


def test_duplicate_request_is_ignored(tmp_path, monkeypatch):
    qt()
    controller = Controller(qt(), tmp_path / "smoke.json")
    controller.worker = object()
    monkeypatch.setattr(controller, "start_worker", lambda _: (_ for _ in ()).throw(AssertionError("Duplicate API call")))
    controller.analyze()
    assert "未重复调用" in controller.overlay.status.text()
    controller.worker = None
    controller.cleanup()
    controller.overlay.hide()
    controller.overlay.deleteLater()


def test_unsupported_state_not_sent_to_api(state_data, monkeypatch):
    qt()
    state_data["game_state"] = "MENU"
    state = parse_state(json.dumps(state_data))
    monkeypatch.setattr("main.StateRefresher.refresh", lambda _: state)
    monkeypatch.setattr("main.DeepSeekClient.decide", lambda *_: (_ for _ in ()).throw(AssertionError("Must not call API")))
    worker = Worker("unit-test-only-value", Settings())
    errors = []
    worker.error.connect(errors.append)
    worker.run()
    assert errors and "MENU" in errors[0]


def test_controller_routes_toggle_to_game_panel_not_desktop(tmp_path):
    qt()
    controller = Controller(qt(), tmp_path / "smoke.json")
    controller.hud.enabled = True
    controller.hud.flush()
    controller.overlay.hide()
    ready = controller.hud.root / "balatro_copilot_hud_ready.txt"
    ready.write_text(f"BACP_READY_V1|{int(time.time())}|100|0|1|ok")
    controller.hotkey_toggle()
    fields = (controller.hud.root / "balatro_copilot_hud.txt").read_text().splitlines()
    assert fields[3] == "0"
    assert not controller.overlay.isVisible()
    controller.cleanup()
    controller.overlay.deleteLater()
