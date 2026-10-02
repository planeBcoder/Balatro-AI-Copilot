"""Install only our display mod; preserve unrelated mods and original game files."""
from pathlib import Path
import json
import os
import shutil


def precision_exporter_text(src: str) -> str:
    marker='-- BALATRO_COPILOT_BINARY64_V1'
    if marker in src:return src
    anchor='        return tostring(value)\n    end\n    if kind == "string"'
    if src.count(anchor)!=1 or src.count('        state.request_id = token')!=1:
        raise OSError('Unknown exporter numeric encoder; refusing to overwrite')
    src=src.replace(anchor,'        '+marker+'\n        return string.format("%.17g", value)\n    end\n    if kind == "string"',1)
    rule='state.scoring_rules = {version = 2,'
    if src.count(rule)!=1:raise OSError('Scoring snapshot is missing')
    return src.replace(rule,rule+' numeric_precision = "binary64",',1)


def upgrade_numeric_precision(game_dir: Path) -> str:
    path=game_dir/'Mods/BalatroStateExporter/main.lua'
    src=path.read_text(encoding='utf-8')
    updated=precision_exporter_text(src)
    if updated==src:return 'ready'
    backup=path.with_name('main.lua.pre-binary64.bak')
    if not backup.exists():shutil.copy2(path,backup)
    tmp=path.with_suffix('.precision.tmp')
    tmp.write_text(updated,encoding='utf-8');os.replace(tmp,path)
    return 'installed'


def install_hud(game_dir: Path, assets: Path) -> str:
    target = game_dir / "Mods" / "BalatroCopilotHUD"
    manifest = target / "manifest.json"
    if target.exists():
        if not manifest.exists() or json.loads(manifest.read_text(encoding="utf-8")).get("id") != "balatro_copilot_hud":
            raise OSError("Existing mod folder is not owned by Copilot")
    target.mkdir(parents=True, exist_ok=True)
    changed = False
    for name in ("main.lua", "manifest.json"):
        source = assets / "game_hud" / name
        destination = target / name
        data = source.read_bytes()
        if destination.exists() and destination.read_bytes() == data:
            continue
        if destination.exists():
            backup = destination.with_name(name + ".pre-update.bak")
            if not backup.exists():
                shutil.copy2(destination, backup)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, destination)
        changed = True
    return "installed" if changed else "ready"


def upgrade_fingerprint(game_dir: Path) -> str:
    path = game_dir / "Mods" / "BalatroStateExporter" / "main.lua"
    src = path.read_text(encoding="utf-8")
    if "-- BALATRO_COPILOT_HUD_FINGERPRINT_V1" in src:
        return "ready"
    anchor = "        state.request_id = token"
    if "-- BALATRO_COPILOT_BRIDGE_V1" not in src or src.count(anchor) != 1:
        raise OSError("Unknown exporter bridge; refusing to overwrite")
    backup = path.with_name("main.lua.pre-game-hud.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    addition = '''
        -- BALATRO_COPILOT_HUD_FINGERPRINT_V1
        local hud = rawget(_G, "BALATRO_COPILOT_HUD_V1")
        if hud then
            local ok, signature = pcall(hud.fingerprint)
            if ok then state.hud_fingerprint = signature end
        end'''
    tmp = path.with_suffix(".hud.tmp")
    tmp.write_text(src.replace(anchor, anchor + addition), encoding="utf-8")
    os.replace(tmp, path)
    return "installed"


def install_autoplay(game_dir: Path, assets: Path) -> str:
    """Install only the separately identified opt-in executor and metadata hook."""
    target = game_dir / "Mods" / "BalatroCopilotAutoPlay"
    manifest = target / "manifest.json"
    if target.exists() and (not manifest.exists() or json.loads(manifest.read_text(encoding="utf-8")).get("id") != "balatro_copilot_autoplay"):
        raise OSError("Existing autoplay folder is not owned by Copilot")
    target.mkdir(parents=True, exist_ok=True)
    changed = False
    for name in ("main.lua", "manifest.json"):
        data = (assets / "autoplay" / name).read_bytes()
        dest = target / name
        if dest.exists() and dest.read_bytes() == data:
            continue
        if dest.exists() and not dest.with_name(name+".pre-update.bak").exists():
            shutil.copy2(dest, dest.with_name(name+".pre-update.bak"))
        tmp = dest.with_suffix(dest.suffix+".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        changed = True
    path = game_dir / "Mods" / "BalatroStateExporter" / "main.lua"
    src = path.read_text(encoding="utf-8")
    marker = "-- BALATRO_COPILOT_AUTO_SNAPSHOT_V1"
    if marker not in src:
        anchor = "        state.request_id = token"
        if "-- BALATRO_COPILOT_BRIDGE_V1" not in src or src.count(anchor) != 1:
            raise OSError("Unknown exporter bridge")
        backup = path.with_name("main.lua.pre-autoplay.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
        src = src.replace(anchor, anchor+"\n"+(assets / "autoplay_snapshot.lua").read_text(encoding="utf-8"))
        scoring_anchor = 'and id ~= "balatro_state_exporter" and id ~= "balatro_copilot_hud"'
        if src.count(scoring_anchor) == 1 and 'id ~= "balatro_copilot_autoplay"' not in src:
            src = src.replace(scoring_anchor, scoring_anchor+' and id ~= "balatro_copilot_autoplay"', 1)
        tmp = path.with_suffix(".auto.tmp")
        tmp.write_text(src, encoding="utf-8")
        os.replace(tmp, path)
        changed = True
    return "installed" if changed else "ready"


def upgrade_scoring_snapshot(game_dir: Path, assets: Path) -> str:
    path = game_dir / "Mods" / "BalatroStateExporter" / "main.lua"
    src = path.read_text(encoding="utf-8")
    if "-- BALATRO_COPILOT_SCORING_SNAPSHOT_V2" in src:
        return "ready"
    anchor = "        state.request_id = token"
    if "-- BALATRO_COPILOT_BRIDGE_V1" not in src or src.count(anchor) != 1:
        raise OSError("Unknown exporter bridge; refusing to overwrite")
    backup = path.with_name("main.lua.pre-strategy-v2.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    addition = (assets / "scoring_snapshot.lua").read_text(encoding="utf-8")
    tmp = path.with_suffix(".strategy.tmp")
    tmp.write_text(src.replace(anchor, anchor+"\n"+addition), encoding="utf-8")
    os.replace(tmp, path)
    return "installed"


def upgrade_shop_snapshot(game_dir: Path, assets: Path) -> str:
    path=game_dir/'Mods/BalatroStateExporter/main.lua'
    src=path.read_text(encoding='utf-8')
    marker='-- BALATRO_COPILOT_SHOP_SNAPSHOT_V1'
    snippet=(assets/'shop_snapshot.lua').read_text(encoding='utf-8')
    if snippet in src:return 'ready'
    anchor='        state.request_id = token'
    if '-- BALATRO_COPILOT_BRIDGE_V1' not in src or src.count(anchor)!=1:
        raise OSError('Unknown exporter; refusing shop upgrade')
    backup=path.with_name('main.lua.pre-shop-advice.bak')
    if not backup.exists():shutil.copy2(path,backup)
    if marker in src:
        start=src.index('        '+marker)
        tail='                used_vouchers=scalar_copy(g.used_vouchers,3)}\n        end\n'
        if src.count(tail)!=1:raise OSError('Unknown shop hook; refusing replacement')
        end=src.index(tail,start)+len(tail)
        end_marker='        -- BALATRO_COPILOT_SHOP_SNAPSHOT_END_V1\n'
        if src[end:].startswith(end_marker):end+=len(end_marker)
        updated=src[:start]+snippet+src[end:]
    else:updated=src.replace(anchor,anchor+'\n'+snippet)
    tmp=path.with_suffix('.shop.tmp')
    tmp.write_text(updated,encoding='utf-8')
    os.replace(tmp,path)
    return 'installed'

