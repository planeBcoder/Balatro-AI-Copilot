from dataclasses import replace
import json
import zlib

import pytest

from core.display_compat import DisplayCompatibility, WindowSnapshot, WindowsDisplay


class FakeDisplay:
    def __init__(self):
        self.window = WindowSnapshot(123, 456, (0, 0, 1920, 1080), (0, 0, 1920, 1080), True)
        self.calls = []
        self.focus = 123
        self.fail = False

    def foreground(self):
        return self.focus

    def snapshot(self, hwnd):
        return self.window if self.window and self.window.hwnd == hwnd else None

    def resize(self, hwnd, rect):
        self.calls.append((hwnd, rect))
        if self.fail:
            return False
        self.window = replace(self.window, rect=rect)
        return True


@pytest.fixture
def setup_display(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    path = game_dir / "settings.jkr"
    path.write_bytes(zlib.compress(b'return {["WINDOW"]={["screenmode"]="Borderless"}}'))
    backend = FakeDisplay()
    compat = DisplayCompatibility(tmp_path / "copilot", game_dir, backend)
    return compat, backend, path


def test_apply_once_restore_and_never_write_game_settings(setup_display):
    compat, backend, path = setup_display
    before = path.read_bytes()
    compat.set_enabled(True)
    assert backend.window.rect == (0, 1, 1920, 1080)
    assert backend.window.rect[3] == backend.window.monitor[3]  # Cover bottom/taskbar.
    assert len(backend.calls) == 1 and compat.path.is_file()
    for _ in range(10):
        compat.tick()
    assert len(backend.calls) == 1
    compat.set_enabled(False)
    assert backend.window.rect == (0, 0, 1920, 1080)
    assert len(backend.calls) == 2 and not compat.path.exists()
    assert path.read_bytes() == before


def test_crash_record_recovered_then_restored(setup_display):
    compat, backend, path = setup_display
    compat.set_enabled(True)
    restarted = DisplayCompatibility(compat.path.parent, path.parent, backend)
    restarted.set_enabled(True)
    assert len(backend.calls) == 1  # No cumulative shrink after a restart.
    restarted.set_enabled(False)
    assert backend.window.rect == (0, 0, 1920, 1080)


@pytest.mark.parametrize("change", ["size", "pid", "monitor", "closed", "mode"])
def test_do_not_overwrite_user_changes_or_reused_handles(setup_display, change):
    compat, backend, path = setup_display
    compat.set_enabled(True)
    if change == "size":
        backend.window = replace(backend.window, rect=(0, 0, 1600, 900))
    elif change == "pid":
        backend.window = replace(backend.window, pid=999)
    elif change == "monitor":
        backend.window = replace(backend.window, monitor=(1920, 0, 3840, 1080))
    elif change == "closed":
        backend.window = None
    elif change == "mode":
        path.write_bytes(zlib.compress(b'return {["WINDOW"]={["screenmode"]="Fullscreen"}}'))
    compat.set_enabled(False)
    assert len(backend.calls) == 1
    assert compat.record is None


@pytest.mark.parametrize("mode", [b"Fullscreen", b"Windowed", b"unknown"])
def test_skip_other_game_modes(setup_display, mode):
    compat, backend, path = setup_display
    path.write_bytes(zlib.compress(b'return {["screenmode"]="' + mode + b'"}'))
    compat.set_enabled(True)
    assert not backend.calls


def test_no_resize_for_other_apps_or_nonfullscreen_geometry(setup_display):
    compat, backend, _ = setup_display
    backend.focus = None
    compat.set_enabled(True)
    assert not backend.calls
    backend.focus = 123
    backend.window = replace(backend.window, rect=(100, 100, 1500, 900))
    compat.tick()
    assert not backend.calls


def test_disabled_by_default(setup_display):
    compat, backend, _ = setup_display
    compat.tick()
    assert not backend.calls and not compat.path.exists()


def test_minimized_owned_window_restored_when_it_returns(setup_display):
    compat, backend, _ = setup_display
    compat.set_enabled(True)
    backend.window = replace(backend.window, interactive=False)
    compat.set_enabled(False)
    assert compat.record is not None and len(backend.calls) == 1
    backend.window = replace(backend.window, interactive=True)
    compat.tick()
    assert compat.record is None and len(backend.calls) == 2


def test_resize_failure_not_repeated_every_timer_tick(setup_display):
    compat, backend, _ = setup_display
    backend.fail = True
    compat.set_enabled(True)
    compat.tick()
    assert len(backend.calls) == 1 and compat.record is None


def test_restore_failure_keeps_recovery_journal(setup_display):
    compat, backend, _ = setup_display
    compat.set_enabled(True)
    backend.fail = True
    compat.set_enabled(False)
    assert compat.record is not None and compat.path.exists()
    backend.fail = False
    compat.tick()
    assert compat.record is None


def test_journal_written_before_resize(setup_display, monkeypatch):
    compat, backend, _ = setup_display
    monkeypatch.setattr(compat, "persist", lambda _: (_ for _ in ()).throw(OSError("Cannot journal")))
    with pytest.raises(OSError):
        compat.set_enabled(True)
    assert not backend.calls


@pytest.mark.parametrize("record", [None, [], {}, {"hwnd": True, "pid": 1}, {"hwnd":123,"pid":456,"original":[0,0,1920,1080],"adjusted":[0,0,1920,900]}])
def test_invalid_restore_records_are_never_acted_on(setup_display, record):
    compat, backend, path = setup_display
    compat.path.parent.mkdir()
    compat.path.write_text(json.dumps(record))
    loaded = DisplayCompatibility(compat.path.parent, path.parent, backend)
    loaded.tick()
    assert not backend.calls


@pytest.mark.parametrize("monitor", [(-1920,0,0,1080), (0,-1080,1920,0)])
def test_negative_monitor_coordinates_supported(setup_display, monitor):
    compat, backend, _ = setup_display
    backend.window = replace(backend.window, rect=monitor, monitor=monitor)
    compat.set_enabled(True)
    left, top, right, bottom = monitor
    assert backend.window.rect == (left,top+1,right,bottom)
    compat.set_enabled(False)
    assert backend.window.rect == monitor


@pytest.mark.parametrize("enabled", [True, False])
def test_legacy_bottom_gap_journal_restored_or_migrated(setup_display, enabled):
    compat, backend, path = setup_display
    backend.window = replace(backend.window, rect=(0,0,1920,1079))
    legacy = dict(hwnd=123, pid=456, original=[0,0,1920,1080], adjusted=[0,0,1920,1079])
    compat.persist(legacy)
    restarted = DisplayCompatibility(compat.path.parent, path.parent, backend)
    restarted.set_enabled(enabled)
    assert backend.window.rect == ((0,1,1920,1080) if enabled else (0,0,1920,1080))
    assert backend.calls[0][1] == (0,0,1920,1080)
    if enabled:
        assert restarted.record["adjusted"] == [0,1,1920,1080]
    else:
        assert restarted.record is None


def test_nonborderless_style_is_not_adjusted(setup_display):
    compat, backend, _ = setup_display
    backend.window = replace(backend.window, borderless=False)
    compat.set_enabled(True)
    assert not backend.calls


def test_windows_backend_does_not_target_ordinary_app_windows():
    assert WindowsDisplay().snapshot(0) is None
