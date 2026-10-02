import json
from pathlib import Path
import threading
import time
import pytest
from core.autoplay import AutoRunner,AutoStopped,AutoTransport
from core.calculation import candidates
from core.state_schema import parse_state
from core.errors import CopilotError
from core.hud_install import install_autoplay,upgrade_scoring_snapshot
from test_game_hud import run_lua_contract,ASSETS


@pytest.fixture
def auto_state():
    s=parse_state((Path(__file__).parent / 'fixtures/observed_finish_state.json').read_bytes())
    s.__pydantic_extra__.update(scoring_rules={'version':2,'back_key':'b_red','other_mods':[],'modifiers':{}},
        autoplay={'version':1,'session':'a'*64,'signature':'b'*64,'ready':True,'card_ids':list(range(1,len(s.hand)+1)),'blind_id':'test-blind'})
    return s


def test_executor_lua_contract():
    contract=Path(__file__).with_name('autoplay_contract.lua').read_text(encoding='utf-8')
    run_lua_contract(contract.replace('__AUTO_SOURCE__',json.dumps(str(ASSETS / 'autoplay/main.lua').replace('\\','/'))))


def test_session_and_physical_card_ids_not_sent_to_ai(auto_state):
    assert 'autoplay' not in auto_state.ai_payload()


def test_transport_default_inactive_and_stop_revokes(tmp_path):
    transport=AutoTransport(tmp_path)
    assert not transport.active and not list(tmp_path.iterdir())
    transport.start()
    assert (tmp_path / 'balatro_copilot_auto_lease.txt').read_text().splitlines()[-1]=='1'
    transport.stop()
    assert not transport.active and (tmp_path / 'balatro_copilot_auto_lease.txt').read_text().splitlines()[-1]=='0'


def test_transport_cancel_does_not_write_command(tmp_path,auto_state):
    transport=AutoTransport(tmp_path)
    transport.start()
    with pytest.raises(AutoStopped):
        transport.execute(auto_state,candidates(auto_state)['candidates'][0],lambda:True)
    assert not (tmp_path / 'balatro_copilot_auto_command.txt').exists()


def test_matching_ack_not_old_ack(tmp_path,auto_state):
    transport=AutoTransport(tmp_path)
    transport.start()
    (tmp_path / 'balatro_copilot_auto_ack.txt').write_text('BACP_AUTO_ACK_V1|wrong|accepted|invoked')
    def game():
        path=tmp_path / 'balatro_copilot_auto_command.txt'
        deadline=time.monotonic()+1
        while not path.exists() and time.monotonic()<deadline: time.sleep(.01)
        fields=path.read_text().splitlines()
        assert fields[4:7]==['a'*64,'b'*64,'play']
        assert fields[7]=='1,2,3,7'
        (tmp_path / 'balatro_copilot_auto_ack.txt').write_text('BACP_AUTO_ACK_V1|'+fields[1]+'|accepted|invoked')
    thread=threading.Thread(target=game)
    thread.start()
    transport.execute(auto_state,candidates(auto_state)['candidates'][0],lambda:False)
    thread.join()


class FakeTransport:
    active=True
    token='c'*32
    def __init__(self): self.commands=[]
    def execute(self,state,candidate,cancelled): self.commands.append(candidate)


def runner(states,transport):
    iterator=iter(states)
    def refresh(): return next(iterator)
    client=type('NoAPI',(),{'decide':lambda *_:pytest.fail('Finish should not call API')})()
    r=AutoRunner(refresh,client,transport,500,lambda:False)
    r.pause=lambda _:None
    return r


def test_runner_finish_execute_once_then_stop_at_settlement(auto_state):
    end=auto_state.model_copy(deep=True)
    end.hands_left-=1
    end.game_state='ROUND_EVAL'
    transport=FakeTransport()
    r=runner([auto_state,auto_state,end,end],transport)
    with pytest.raises(AutoStopped,match='已结束'):
        r.run()
    assert len(transport.commands)==1 and transport.commands[0]['action']=='play'
    assert r.model_decisions==0


def test_runner_stale_decision_never_executes(auto_state):
    changed=auto_state.model_copy(deep=True)
    changed.autoplay['signature']='d'*64
    transport=FakeTransport()
    with pytest.raises(AutoStopped,match='旧决策未执行'):
        runner([auto_state,changed],transport).run()
    assert not transport.commands


def test_runner_unexpected_resource_changes_never_issue_another_move(auto_state):
    changed=auto_state.model_copy(deep=True)
    changed.hands_left-=2
    transport=FakeTransport()
    with pytest.raises(AutoStopped,match='资源变化'):
        runner([auto_state,auto_state,changed],transport).run()
    assert len(transport.commands)==1


def test_runner_snapshot_read_error_does_not_repeat_action(auto_state):
    end=auto_state.model_copy(deep=True)
    end.game_state='ROUND_EVAL'
    end.hands_left-=1
    transport=FakeTransport()
    r=runner([],transport)
    items=iter([auto_state,auto_state,CopilotError('partial transient snapshot'),end,end])
    def refresh():
        s=next(items)
        if isinstance(s,Exception):raise s
        return s
    r.refresh=refresh
    with pytest.raises(AutoStopped):r.run()
    assert len(transport.commands)==1


def test_runner_unsupported_effects_stop_before_ai_or_action(auto_state):
    auto_state.jokers=[{'key':'j_blueprint'}]
    transport=FakeTransport()
    with pytest.raises(CopilotError,match='无法完整验算'):
        runner([auto_state],transport).run()
    assert not transport.commands


@pytest.mark.parametrize('kind',['SHOP','MENU','GAME_OVER','ROUND_EVAL'])
def test_runner_does_not_automate_other_screens(auto_state,kind):
    auto_state.game_state=kind
    transport=FakeTransport()
    with pytest.raises(AutoStopped): runner([auto_state],transport).run()
    assert not transport.commands


def test_runner_game_escape_cancellation_stops(auto_state):
    auto_state.autoplay['cancelled_run']='c'*32
    transport=FakeTransport()
    with pytest.raises(AutoStopped,match='Escape'):
        runner([auto_state],transport).run()
    assert not transport.commands


def test_executor_installer_owned_and_idempotent(tmp_path):
    exporter=tmp_path / 'Mods/BalatroStateExporter/main.lua'
    exporter.parent.mkdir(parents=True)
    source='-- BALATRO_COPILOT_BRIDGE_V1\n        state.request_id = token\n'
    exporter.write_text(source)
    upgrade_scoring_snapshot(tmp_path,ASSETS)
    before=exporter.read_text()
    assert install_autoplay(tmp_path,ASSETS)=='installed'
    assert install_autoplay(tmp_path,ASSETS)=='ready'
    assert exporter.with_name('main.lua.pre-autoplay.bak').read_text()==before
    assert 'pcall(auto.snapshot)' in exporter.read_text()
    assert 'auto.execute' not in exporter.read_text()
    assert 'id ~= "balatro_copilot_autoplay"' in exporter.read_text()


def test_autoplay_settings_default_off_and_not_coerced_from_text(tmp_path):
    from config.settings import Settings,SettingsStore
    store=SettingsStore(tmp_path)
    assert not Settings().auto_play_enabled
    store.path.write_text(json.dumps({'auto_play_enabled':'true'}))
    assert not store.load().auto_play_enabled


def test_stop_before_dispatch_after_model_or_preview(auto_state):
    transport=FakeTransport()
    r=runner([auto_state],transport)
    def pause(_):
        transport.active=False
        r.check_stop()
    r.pause=pause
    with pytest.raises(AutoStopped):r.run()
    assert not transport.commands


def test_auto_ui_start_requires_explicit_opt_in(tmp_path,monkeypatch):
    from test_ui import qt
    from main import Controller
    controller=Controller(qt(),tmp_path / 'smoke.json')
    controller.smoke=None
    opened=[]
    monkeypatch.setattr(controller,'open_settings',lambda:opened.append(True))
    controller.toggle_auto()
    assert opened and controller.auto_worker is None and not controller.auto_transport.active
    controller.cleanup()
    controller.overlay.hide()
    controller.overlay.deleteLater()


def test_auto_ui_emergency_stop_revokes_pending_worker(tmp_path):
    from test_ui import qt
    from main import Controller
    controller=Controller(qt(),tmp_path / 'smoke.json')
    controller.auto_transport.start()
    interrupted=[]
    controller.auto_worker=type('Pending',(),{'requestInterruption':lambda _:interrupted.append(True)})()
    controller.stop_auto()
    assert interrupted and not controller.auto_transport.active
    assert (controller.auto_transport.root / 'balatro_copilot_auto_lease.txt').read_text().splitlines()[-1]=='0'
    controller.auto_worker=None
    controller.cleanup()
    controller.overlay.hide()
    controller.overlay.deleteLater()
