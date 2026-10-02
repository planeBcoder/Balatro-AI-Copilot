"""Construct a local, normal-rules game copy. Never ship game assets."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile
from config.settings import GAME_DIR, resource
from core.hud_install import precision_exporter_text

DEFAULT_SOURCE = None
LAB_PROFILE = GAME_DIR.parent / 'BalatroCopilotLab'


def build_lab(destination: Path, report: Path, source: Path | None = DEFAULT_SOURCE, profile_name: str = 'BalatroCopilotLab') -> Path:
    if profile_name not in {'BalatroCopilotLab','BalatroCopilotShopLab'}:
        raise ValueError('Only isolated Copilot profiles are permitted')
    lab_profile = LAB_PROFILE if profile_name == 'BalatroCopilotLab' else GAME_DIR.parent / profile_name
    if source is None:
        from core.game_detection import selected_game
        source=selected_game()
    exe = source / 'Balatro.exe'
    if not exe.is_file():
        raise FileNotFoundError('未找到已安装的 Balatro：' + str(exe))
    exporter = GAME_DIR / 'Mods/BalatroStateExporter/main.lua'
    if not exporter.is_file():
        raise FileNotFoundError('请先安装本机状态导出器。')
    destination.mkdir(parents=True, exist_ok=True)
    lab_profile.mkdir(parents=True, exist_ok=True)
    for rel in ['settings.jkr', '1/profile.jkr', '1/meta.jkr']:
        target = lab_profile / rel
        if not target.exists() and (GAME_DIR / rel).exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(GAME_DIR / rel, target)
    for p in source.iterdir():
        if p.is_file() and p.name.lower() not in {'balatro.exe', 'winmm.dll'}:
            shutil.copy2(p, destination / p.name)
    raw = exe.read_bytes()
    target = destination / 'BalatroCopilotLab.exe'
    preserved = {}
    with zipfile.ZipFile(exe) as original:
        prefix = raw[:min(i.header_offset for i in original.infolist())]
        target.write_bytes(prefix)
        with zipfile.ZipFile(target, 'a', zipfile.ZIP_DEFLATED) as z:
            for item in original.infolist():
                data = original.read(item.filename)
                if item.filename == 'conf.lua':
                    data = data.decode().replace("t.title = 'Balatro'", f"t.title = '{profile_name}'\n    t.identity = '{profile_name}'").encode()
                    if f"t.identity = '{profile_name}'".encode() not in data:
                        raise ValueError('游戏配置不兼容，未启动副本。')
                elif item.filename == 'main.lua':
                    data += b'\nrequire("copilot_lab")\n'
                else:
                    preserved[item.filename] = hashlib.sha256(data).hexdigest()
                z.writestr(item, data)
            text = precision_exporter_text(exporter.read_text(encoding='utf-8')).replace('local MOD = SMODS.current_mod', 'local MOD = nil')
            z.writestr('copilot_exporter.lua', text)
            for name, asset in [('copilot_hud.lua', 'game_hud/main.lua'), ('copilot_auto.lua', 'autoplay/main.lua'), ('copilot_run.lua', 'fullrun/main.lua')]:
                z.writestr(name, resource('assets/' + asset).read_bytes())
            z.writestr('copilot_lab.lua', '''
assert(love.filesystem.getIdentity()=='BalatroCopilotLab','Isolated identity required')
require('copilot_hud')
require('copilot_auto')
require('copilot_run')
require('copilot_exporter')
local previous_error=love.errorhandler
function love.errorhandler(message)
    love.filesystem.write('copilot-runtime-error.txt',tostring(message)..'\\n'..debug.traceback())
    return previous_error(message)
end
'''.replace("=='BalatroCopilotLab'",f"=='{profile_name}'"))
    with zipfile.ZipFile(target) as z:
        assert all(hashlib.sha256(z.read(n)).hexdigest() == v for n, v in preserved.items())
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({'original': str(exe), 'lab': str(target), 'identity': profile_name, 'unchanged_entries': len(preserved), 'critical_logic': {n: preserved[n] for n in ['card.lua', 'game.lua', 'functions/state_events.lua', 'functions/button_callbacks.lua', 'blind.lua', 'back.lua']}, 'only_original_changes': ['conf.lua: isolated identity and title', 'main.lua: append instrumentation require'], 'original_profile_modified': False}, indent=2), encoding='utf-8')
    return target
