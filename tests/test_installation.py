import hashlib
import json
from pathlib import Path
import zipfile
import pytest
from core import installation as install
from core.game_detection import inspect_game,discover_games,parse_vdf

ROOT=Path(__file__).resolve().parents[1]


def fake_game(folder,version='1.0.1o',full=True):
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/'Balatro.exe';path.write_bytes(b'MZ-test-only')
    with zipfile.ZipFile(path,'a') as archive:
        archive.writestr('globals.lua',f"VERSION = '{version}'\n"+("VERSION = VERSION..'-FULL'" if full else ''))
        for name in ('main.lua','game.lua','card.lua','conf.lua'):archive.writestr(name,'-- synthetic test fixture')
    return folder


@pytest.fixture
def integration(tmp_path):
    deps=ROOT/'dependencies'
    if not (deps/'lovely-v0.10.0.zip').exists():pytest.skip('Run scripts/prepare-dependencies.ps1 first')
    game=fake_game(tmp_path/'game')
    profile=tmp_path/'profile';(profile/'1').mkdir(parents=True)
    (profile/'1/save.jkr').write_bytes(b'untouched save')
    return install.Integration(game,profile,tmp_path/'app',ROOT/'assets',deps)


def test_version_and_demo_fail_closed(tmp_path):
    assert inspect_game(fake_game(tmp_path/'good')).supported
    assert not inspect_game(fake_game(tmp_path/'demo',full=False)).supported
    assert not inspect_game(fake_game(tmp_path/'future',version='2.0.0')).supported


def test_steam_library_discovery(tmp_path):
    root=tmp_path/'steam';(root/'steamapps').mkdir(parents=True)
    lib=tmp_path/'another library'
    game=fake_game(lib/'steamapps/common/Balatro')
    value=str(lib).replace('\\','\\\\')
    (root/'steamapps/libraryfolders.vdf').write_text('"libraryfolders" { "1" { "path" "'+value+'" } }')
    assert [g.path for g in discover_games([root])]==[game.resolve()]


@pytest.mark.parametrize('text',['"root" { "key"','}','"a" {'])
def test_invalid_vdf(text):
    with pytest.raises(ValueError):parse_vdf(text)


def test_new_install_repair_and_uninstall(integration):
    original=integration.game.joinpath('Balatro.exe').read_bytes()
    result=integration.install();assert result['changed']>6
    assert integration.install()['changed']==0
    src=(integration.profile/'Mods/BalatroStateExporter/main.lua').read_text(encoding='utf-8')
    for marker in ('SCORING_SNAPSHOT_V2','SHOP_SNAPSHOT_V1','AUTO_SNAPSHOT_V1','BINARY64_V1','HUD_FINGERPRINT_V1'):
        assert marker in src
    assert integration.uninstall()=={'removed':6,'preserved':[]}
    assert not (integration.profile/'Mods/BalatroCopilotHUD').exists()
    assert (integration.profile/'Mods/Steamodded/version.lua').exists()
    assert (integration.game/'winmm.dll').exists()  # Shared dependencies deliberately retained.
    assert (integration.profile/'1/save.jkr').read_bytes()==b'untouched save'
    assert integration.game.joinpath('Balatro.exe').read_bytes()==original


def test_existing_owned_exporter_restored(integration):
    folder=integration.profile/'Mods/BalatroStateExporter';folder.mkdir(parents=True)
    (folder/'manifest.json').write_text(json.dumps({'id':install.MOD_IDS[folder.name]}))
    (folder/'main.lua').write_bytes(b'original exporter')
    integration.install();integration.uninstall()
    assert (folder/'main.lua').read_bytes()==b'original exporter'


@pytest.mark.parametrize('name',['winmm.dll','version.dll'])
def test_conflicting_loader_preserved_without_any_write(integration,name):
    (integration.game/name).write_bytes(b'unknown loader')
    with pytest.raises(OSError):integration.install()
    assert not (integration.profile/'Mods').exists()
    assert (integration.game/name).read_bytes()==b'unknown loader'


def test_foreign_mod_folder_is_not_overwritten(integration):
    folder=integration.profile/'Mods/BalatroCopilotHUD';folder.mkdir(parents=True)
    (folder/'main.lua').write_bytes(b'foreign')
    with pytest.raises(OSError):integration.install()
    assert (folder/'main.lua').read_bytes()==b'foreign'
    assert not (integration.game/'winmm.dll').exists()


def test_game_running_rejected(integration):
    integration.running=lambda:True
    with pytest.raises(OSError,match='退出'):integration.install()
    assert not (integration.game/'winmm.dll').exists()


def test_concurrent_installation_is_rejected(integration):
    from core.single_instance import SingleInstance
    guard=SingleInstance('BalatroAICopilotIntegration')
    try:
        with pytest.raises(OSError,match='另一个'):integration.install()
        assert not (integration.game/'winmm.dll').exists()
    finally:guard.close()


def test_partial_write_rolls_back(integration,monkeypatch):
    real=install.atomic
    calls=0
    def fault(path,data):
        nonlocal calls
        if Path(path).is_relative_to(integration.profile):
            calls+=1
            if calls==2:raise OSError('Injected interrupted disk write')
        return real(path,data)
    monkeypatch.setattr(install,'atomic',fault)
    with pytest.raises(OSError,match='Injected'):integration.install()
    assert not (integration.game/'winmm.dll').exists()
    assert not integration.ledger.exists() and not integration.journal.exists()
    assert not any(p.is_file() for p in (integration.profile/'Mods').rglob('*'))


def test_user_modified_file_preserved_on_repair_and_uninstall(integration):
    integration.install()
    path=integration.profile/'Mods/BalatroCopilotHUD/main.lua';path.write_bytes(b'my changes')
    with pytest.raises(OSError,match='修改'):integration.install()
    assert integration.uninstall()['preserved']==['Mods/BalatroCopilotHUD/main.lua']
    assert path.read_bytes()==b'my changes'


@pytest.mark.parametrize('path',['../save.jkr','Mods/../../save.jkr','C:/save.jkr','Mods\\save.jkr','/save.jkr'])
def test_bounds_reject_traversal(tmp_path,path):
    with pytest.raises(OSError):install.bounded(tmp_path,path)


def test_tampered_dependency_rejected(integration,tmp_path):
    deps=tmp_path/'bad-deps';deps.mkdir()
    (deps/'dependencies.lock.json').write_bytes((integration.dependencies/'dependencies.lock.json').read_bytes())
    (deps/'lovely-v0.10.0.zip').write_bytes(b'tampered')
    integration.dependencies=deps
    with pytest.raises(OSError,match='校验'):integration.install()
    assert not (integration.game/'winmm.dll').exists()


def test_all_exporter_templates_parse_in_real_lua(integration):
    from test_game_hud import run_lua_contract
    src=install.exporter_text(integration.assets)
    run_lua_contract('local fn,err=loadstring('+json.dumps(src)+'); assert(fn,err)')
