import ctypes
import faulthandler
import json
from pathlib import Path
from urllib.parse import unquote

import pytest

from config.settings import Settings, SettingsStore
from core.game_hud import GameHUD
from core.hud_install import install_hud, upgrade_fingerprint
from core.state_schema import parse_state

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def test_disabled_hud_writes_nothing(tmp_path):
    h = GameHUD(tmp_path)
    h.message("idle", "显示内容")
    assert not list(tmp_path.iterdir())


def test_protocol_unicode_newlines_no_code_execution(tmp_path):
    h = GameHUD(tmp_path, lambda: 100)
    h.enabled = True
    h.message("result", '弃掉 ♥A\n理由：loadstring("bad")', "a"*64)
    fields = (tmp_path / "balatro_copilot_hud.txt").read_text().splitlines()
    assert b"\r" not in (tmp_path / "balatro_copilot_hud.txt").read_bytes()
    assert len(fields) == 7 and fields[:2] == ["BACP_HUD_V1", "100"]
    assert fields[4:6] == ["result", "a"*64]
    assert unquote(fields[6]) == '弃掉 ♥A\n理由：loadstring("bad")'
    assert not list(tmp_path.glob("*.tmp"))


def test_result_without_game_signature_fails_closed(tmp_path):
    h = GameHUD(tmp_path)
    h.enabled = True
    h.message("result", "Do not show unverifiable advice")
    assert h.kind == "error" and "重启" in h.text


@pytest.mark.parametrize("text", ["中"*20000, "😀"*20000, "x\0\1\n\t中文"], ids=["long-cjk", "long-emoji", "controls"])
def test_payload_bounded_and_controls_removed(tmp_path, text):
    h = GameHUD(tmp_path)
    h.enabled = True
    h.message("idle", text)
    assert (tmp_path / "balatro_copilot_hud.txt").stat().st_size <= 64000
    assert "\0" not in h.text and "\1" not in h.text


@pytest.mark.parametrize("ready", ["BACP_READY_V1|90|10|0|1|ok", "BACP_READY_V1|101|10|0|1|ok", "BACP_READY_V1|100|0|0|1|ok", "BACP_READY_V1|100|10|0|1|error", "junk"])
def test_reject_stale_future_unrendered_and_failed_ready(tmp_path, ready):
    h = GameHUD(tmp_path, lambda: 100)
    (tmp_path / "balatro_copilot_hud_ready.txt").write_text(ready)
    assert h.ready() is None


def test_mouse_collapse_ack_and_global_toggle(tmp_path):
    h = GameHUD(tmp_path, lambda: 100)
    h.enabled = True
    (tmp_path / "balatro_copilot_hud_ready.txt").write_text("BACP_READY_V1|100|10|0|0|ok")
    assert h.ready() and not h.expanded
    h.toggle()
    assert h.expanded and h.seq == 1


def test_not_connected_until_game_parses_current_message(tmp_path):
    h = GameHUD(tmp_path, lambda: 100)
    h.enabled = True
    h.message("idle", "current message")
    ready = tmp_path / "balatro_copilot_hud_ready.txt"
    ready.write_text("BACP_READY_V1|100|10|-1|1|ok")
    assert h.ready() is None
    ready.write_text("BACP_READY_V1|100|10|0|1|ok")
    assert h.ready() is None
    ready.write_text(f"BACP_READY_V1|100|10|{h.seq}|1|ok")
    assert h.ready() is not None


def test_install_idempotent_and_exporter_backup(tmp_path):
    assert install_hud(tmp_path, ASSETS) == "installed"
    assert install_hud(tmp_path, ASSETS) == "ready"
    exporter = tmp_path / "Mods/BalatroStateExporter/main.lua"
    exporter.parent.mkdir()
    source = '-- BALATRO_COPILOT_BRIDGE_V1\n        state.request_id = token\n'
    exporter.write_text(source)
    assert upgrade_fingerprint(tmp_path) == "installed"
    assert exporter.with_name("main.lua.pre-game-hud.bak").read_text() == source
    assert upgrade_fingerprint(tmp_path) == "ready"


def test_install_refuses_foreign_folder_and_exporter(tmp_path):
    folder = tmp_path / "Mods/BalatroCopilotHUD"
    folder.mkdir(parents=True)
    (folder / "main.lua").write_text("foreign")
    with pytest.raises(OSError):
        install_hud(tmp_path, ASSETS)
    exporter = tmp_path / "Mods/BalatroStateExporter/main.lua"
    exporter.parent.mkdir()
    exporter.write_text("foreign")
    with pytest.raises(OSError):
        upgrade_fingerprint(tmp_path)
    assert exporter.read_text() == "foreign"


def test_hud_settings_disable_pixel_workaround(tmp_path):
    store = SettingsStore(tmp_path)
    store.save(Settings(in_game_hud=True, display_compatibility=True))
    assert store.load().in_game_hud and not store.load().display_compatibility


def test_game_signature_stays_local(state_data):
    state_data["hud_fingerprint"] = "a"*64
    s = parse_state(json.dumps(state_data))
    assert s.hud_fingerprint == "a"*64
    assert "hud_fingerprint" not in s.ai_payload()


def run_lua_contract(contract):
    # Isolated Lua VM using the installed runtime, never attach to the live game.
    from core.game_detection import selected_game
    try:runtime=selected_game()/'lua51.dll'
    except FileNotFoundError:pytest.skip('Install Balatro to run optional real Lua engine contracts')
    if not runtime.exists():
        pytest.skip("Windows game's Lua runtime not installed")
    lib = ctypes.CDLL(str(runtime))
    lib.luaL_newstate.restype = ctypes.c_void_p
    lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
    lib.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
    lib.lua_tolstring.restype = ctypes.c_char_p
    lib.lua_close.argtypes = [ctypes.c_void_p]
    vm = lib.luaL_newstate()
    # LuaJIT uses a caught Windows exception for Lua pcall/error unwinding.
    # Do not print Python fatal-error traces for that intentionally caught path.
    was_enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        lib.luaL_openlibs(vm)
        result = lib.luaL_loadstring(vm, contract.encode("utf-8"))
        if result == 0:
            result = lib.lua_pcall(vm, 0, 0, 0)
        assert result == 0, lib.lua_tolstring(vm, -1, None).decode("utf-8", "replace") if result else ""
    finally:
        lib.lua_close(vm)
        if was_enabled:
            faulthandler.enable()


def test_lua_display_contract_with_real_lua51():
    contract = (Path(__file__).with_name("hud_contract.lua")).read_text(encoding="utf-8")
    contract = contract.replace("__HUD_SOURCE__", json.dumps(str(ASSETS / "game_hud/main.lua").replace("\\", "/")))
    run_lua_contract(contract)


def test_shipped_vanilla_scoring_oracle():
    source = Path(__file__).resolve().parents[3] / "work/balatro-source/card.lua"
    if not source.exists():
        pytest.skip("Read-only extracted vanilla scoring source unavailable")
    contract = Path(__file__).with_name("vanilla_score_contract.lua").read_text(encoding="utf-8")
    run_lua_contract(contract.replace("__CARD_SOURCE__", json.dumps(str(source).replace("\\", "/"))))


def test_real_exporter_scoring_metadata_is_read_only():
    source = Path.home() / "AppData/Roaming/Balatro/Mods/BalatroStateExporter/main.lua"
    if not source.exists() or "BALATRO_COPILOT_SCORING_SNAPSHOT_V2" not in source.read_text(encoding="utf-8"):
        pytest.skip("Installed owned exporter upgrade unavailable")
    contract = Path(__file__).with_name("export_snapshot_contract.lua").read_text(encoding="utf-8")
    run_lua_contract(contract.replace("__EXPORTER_SOURCE__", json.dumps(str(source).replace("\\", "/"))))
