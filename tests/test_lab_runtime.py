import hashlib
import json
import zipfile
import pytest
from core import lab_runtime


def test_lab_builder_preserves_engine_and_original_profile(tmp_path, monkeypatch):
    source = tmp_path / 'installed'
    source.mkdir()
    exe = source / 'Balatro.exe'
    exe.write_bytes(b'MZ-test-only-prefix')
    preserved = ['card.lua','game.lua','functions/state_events.lua','functions/button_callbacks.lua','blind.lua','back.lua']
    with zipfile.ZipFile(exe, 'a') as z:
        z.writestr('conf.lua', "function love.conf(t)\n t.title = 'Balatro'\nend")
        z.writestr('main.lua', '-- Original main')
        for n in preserved:
            z.writestr(n, '-- Original ' + n)
    original_hash = hashlib.sha256(exe.read_bytes()).hexdigest()
    profile = tmp_path / 'original-profile'
    (profile / 'Mods/BalatroStateExporter').mkdir(parents=True)
    (profile / 'Mods/BalatroStateExporter/main.lua').write_text('local MOD = SMODS.current_mod\n-- BALATRO_COPILOT_BINARY64_V1')
    (profile / '1').mkdir()
    (profile / '1/save.jkr').write_bytes(b'User-run-not-copied')
    (profile / '1/meta.jkr').write_bytes(b'Unlocks-copied-once')
    (source / 'love.dll').write_bytes(b'test-dll')
    (source / 'winmm.dll').write_bytes(b'Lovely-excluded')
    monkeypatch.setattr(lab_runtime, 'GAME_DIR', profile)
    lab = tmp_path / 'lab-profile'
    monkeypatch.setattr(lab_runtime, 'LAB_PROFILE', lab)
    destination = tmp_path / 'runtime'
    report = tmp_path / 'report.json'
    built = lab_runtime.build_lab(destination, report, source)
    with zipfile.ZipFile(built) as z:
        assert "t.identity = 'BalatroCopilotLab'" in z.read('conf.lua').decode()
        assert 'require("copilot_lab")' in z.read('main.lua').decode()
        for n in preserved:
            assert z.read(n) == ('-- Original ' + n).encode()
    assert hashlib.sha256(exe.read_bytes()).hexdigest() == original_hash
    assert (profile / '1/save.jkr').read_bytes() == b'User-run-not-copied'
    assert not (lab / '1/save.jkr').exists()
    assert (lab / '1/meta.jkr').read_bytes() == b'Unlocks-copied-once'
    assert not (destination / 'winmm.dll').exists()
    assert json.loads(report.read_text())['original_profile_modified'] is False


def test_builder_requires_installed_game(tmp_path):
    with pytest.raises(FileNotFoundError):
        lab_runtime.build_lab(tmp_path / 'out', tmp_path / 'report', tmp_path / 'missing')
