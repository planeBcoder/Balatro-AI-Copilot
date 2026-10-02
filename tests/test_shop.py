import copy
import json
from pathlib import Path
import pytest
from core.state_schema import parse_state
from core.shop import shop_candidates, shop_ai_payload, local_shop_decision, snapshot
from core.errors import CopilotError
from ai.shop_schema import validate_shop_decision
from ai.deepseek_client import DeepSeekClient, TransportError
from core.presentation import shop_document
from core.hud_install import upgrade_shop_snapshot
from test_game_hud import run_lua_contract, ASSETS


@pytest.fixture
def shop_state():
    s=parse_state((Path(__file__).parent/'fixtures/observed_finish_state.json').read_bytes())
    s.game_state='SHOP';s.money=20
    s.__pydantic_extra__['hud_fingerprint']='a'*64
    s.__pydantic_extra__['scoring_rules']={'version':2,'back_key':'b_red','modifiers':{},'other_mods':[],'numeric_precision':'binary64'}
    full=s.hand+s.deck_remaining
    s.__pydantic_extra__['shop_snapshot']={'version':1,'ready':True,'offers':[
        {'key':'j_joker','name':'Joker','set':'Joker','cost':3,'sell_cost':1,'ability':{'mult':4}},
        {'key':'j_unknown','name':'Unknown','set':'Joker','cost':30,'ability':{}}],
        'boosters':[],'vouchers':[],'jokers':[],'consumables':[],
        'joker_limit':5,'consumable_limit':2,'reroll_cost':5,'full_deck':[c.model_dump() for c in full]}
    return s


def test_price_cash_and_interest_are_local(shop_state):
    before=shop_state.model_dump()
    calc=shop_candidates(shop_state,1)
    buy=next(c for c in calc['candidates'] if c['action']=='buy')
    assert buy['cost']==3 and buy['cash_after']==17
    assert buy['interest_now']==4 and buy['interest_after']==3
    assert buy['build_score']>calc['baseline_score']>0
    assert not any(c.get('target',{}) and c['target']['key']=='j_unknown' for c in calc['candidates'])
    assert shop_state.model_dump()==before  # No state mutation.


def test_unsupported_build_is_unknown_not_zero(shop_state):
    shop_state.model_extra['shop_snapshot']['offers'][0]['key']='j_unknown'
    calc=shop_candidates(shop_state,1)
    assert next(c for c in calc['candidates'] if c['action']=='buy')['build_score'] is None


def test_eternal_swap_excluded_and_sale_allows_purchase(shop_state):
    m=shop_state.model_extra['shop_snapshot'];m['joker_limit']=1
    old={'key':'j_joker','name':'old','set':'Joker','cost':3,'sell_cost':4,'ability':{'mult':4},'eternal':True}
    m['jokers']=[old];m['offers'][0]['cost']=22
    assert not any(c['action']=='swap' for c in shop_candidates(shop_state,1)['candidates'])
    old['eternal']=False
    swap=next(c for c in shop_candidates(shop_state,1)['candidates'] if c['action']=='swap')
    assert swap['cash_after']==2 and swap['sale_proceeds']==4


def test_negative_sale_does_not_invent_slot(shop_state):
    m=shop_state.model_extra['shop_snapshot'];m['joker_limit']=1
    old={'key':'j_joker','set':'Joker','cost':3,'ability':{'mult':4},'edition':'negative'}
    m['jokers']=[old]
    assert not any(c['action']=='swap' for c in shop_candidates(shop_state,1)['candidates'])


def test_consumeable_slots_and_unknown_pack_contents(shop_state):
    m=shop_state.model_extra['shop_snapshot'];m['consumable_limit']=0
    m['offers']=[{'key':'c_mercury','set':'Planet','cost':3,'ability':{'consumeable':{'hand_type':'Pair'}}}]
    m['boosters']=[{'key':'p_celestial_normal_1','set':'Booster','cost':4,'ability':{}}]
    calc=shop_candidates(shop_state,1)
    assert not any(c['action']=='buy' for c in calc['candidates'])
    pack=next(c for c in calc['candidates'] if c['action']=='open')
    assert pack['build_score'] is None and '未知' in pack['local_note']


def test_ai_only_legal_candidates_and_no_guarantees(shop_state):
    calc=shop_candidates(shop_state,1)
    raw={'recommendations':[{'rank':1,'candidate_id':'invalid','reason':'bad'}],'summary':'bad'}
    with pytest.raises(ValueError):validate_shop_decision(json.dumps(raw),calc)
    raw['recommendations'][0].update(candidate_id='s1',reason='保证获胜，胜率 100%')
    with pytest.raises(ValueError):validate_shop_decision(json.dumps(raw),calc)
    raw['recommendations'][0]['reason']='保证获胜'
    decision=validate_shop_decision(json.dumps(raw),calc)
    assert '保证获胜' not in decision.recommendations[0].reason
    raw['recommendations'][0]['action']='buy'
    with pytest.raises(ValueError):validate_shop_decision(json.dumps(raw),calc)


def test_ai_payload_has_no_execution_identity_or_draw_order(shop_state):
    shop_state.model_extra['autoplay']={'session':'secret','fullrun':{'signature':'private'}}
    p=shop_ai_payload(shop_state)
    raw=json.dumps(p)
    assert 'secret' not in raw and 'private' not in raw and 'full_deck' not in raw
    assert sum(p['deck_counts'].values())==len(shop_state.model_extra['shop_snapshot']['full_deck'])


def test_api_review_and_fallback_document(shop_state):
    calc=shop_candidates(shop_state,1)
    calls=[]
    def fake(payload,key,timeout):
        calls.append(payload)
        return {'choices':[{'message':{'content':json.dumps({'recommendations':[{'rank':1,'candidate_id':'s1','reason':'保留利息'}],'summary':'等待更有效的强化'})}}]}
    client=DeepSeekClient('fake','deepseek-flash',ASSETS.parent/'prompts/decision_system.md',fake)
    decision=client.decide_shop(shop_ai_payload(shop_state),calc)
    assert len(calls)==1 and '合法' in calls[0]['messages'][0]['content']
    calc['ai_reviewed']=True
    blocks,plain,hud=shop_document(decision,calc,shop_state,1)
    assert 'AI 判断' in plain and '不代操作' in plain and hud.startswith('BACP_BLOCKS_V1')
    calc['ai_reviewed']=False
    assert 'AI 未参与' in shop_document(local_shop_decision(calc),calc,shop_state,1)[1]


def test_cash_sensitive_bull_uses_after_purchase_money(shop_state):
    m=shop_state.model_extra['shop_snapshot']
    m['offers']=[{'key':'j_bull','set':'Joker','cost':6,'ability':{'extra':2}}]
    c=next(c for c in shop_candidates(shop_state,1)['candidates'] if c['action']=='buy')
    assert c['cash_after']==14 and '+28 筹码' in c['local_note']
    assert c['build_score']>0


def test_missing_or_busy_shop_fails_closed(shop_state):
    shop_state.model_extra['shop_snapshot']['ready']=False
    with pytest.raises(CopilotError):snapshot(shop_state)
    shop_state.model_extra.pop('shop_snapshot')
    with pytest.raises(CopilotError):snapshot(shop_state)


def test_upgrade_own_exporter_is_backed_up_and_idempotent(tmp_path):
    path=tmp_path/'Mods/BalatroStateExporter/main.lua';path.parent.mkdir(parents=True)
    original='-- BALATRO_COPILOT_BRIDGE_V1\n        state.request_id = token\n'
    path.write_text(original)
    assert upgrade_shop_snapshot(tmp_path,ASSETS)=='installed'
    assert path.with_name('main.lua.pre-shop-advice.bak').read_text()==original
    assert upgrade_shop_snapshot(tmp_path,ASSETS)=='ready'


def test_lua_shop_snapshot_read_only_contract():
    snippet=(ASSETS/'shop_snapshot.lua').read_text()
    run_lua_contract('''
state={game_state='SHOP'}
function scalar_copy(v,d)
 if type(v)~='table' then return v end
 local out={}; for k,x in pairs(v) do out[k]=scalar_copy(x,d-1) end; return out
end
function edition_name(v) return nil end
function playing_card(c) return {code='AS'} end
G={GAME={dollars=20,STOP_USE=0,current_round={reroll_cost=5},round_resets={hands=4,discards=3}},
 SETTINGS={paused=false},CONTROLLER={},shop={},jokers={cards={},config={card_limit=5}},
 consumeables={cards={},config={card_limit=2}},hand={config={card_limit=8}},
 shop_jokers={cards={{cost=3,sell_cost=1,ability={name='Joker',set='Joker',mult=4},config={center={key='j_joker'}}}}},
 playing_cards={{base={nominal=11},ability={bonus=30,perma_bonus=2}}}}
G.FUNCS=setmetatable({},{__index=function()error('game callback forbidden')end})
love={filesystem={write=function()error('file write forbidden')end},math={random=function()error('RNG forbidden')end}}
''' + snippet + '''
assert(state.shop_snapshot.ready)
assert(state.shop_snapshot.offers[1].cost==3)
assert(state.shop_snapshot.full_deck[1].base_bonus==30)
state.shop_snapshot.offers[1].ability.mult=999
assert(G.shop_jokers.cards[1].ability.mult==4 and G.GAME.dollars==20)
''')


def test_worker_shop_routes_to_ai_and_overlay(shop_state,monkeypatch):
    from test_ui import qt
    from main import Worker
    from config.settings import Settings
    from app.overlay import Overlay
    qt()
    monkeypatch.setattr('main.StateRefresher.refresh',lambda _:shop_state)
    seen=[]
    def review(_,payload,calc):
        seen.append(payload)
        return validate_shop_decision(json.dumps({'recommendations':[{'rank':1,'candidate_id':'s1','reason':'先保留资金'}],'summary':'等待更合适的组合'}),calc)
    monkeypatch.setattr('main.DeepSeekClient.decide_shop',review)
    w=Worker('fake',Settings());results=[];errors=[]
    w.result.connect(results.append);w.error.connect(errors.append);w.run()
    assert len(results)==1 and len(seen)==1 and not errors
    result=results[0];assert result['calculation']['ai_reviewed']
    overlay=Overlay();overlay.render(result['decision'],result['calculation'],shop_state,1)
    qt().processEvents()
    assert overlay.options_layout.count()==1 and '商店' in overlay.context.text()
    assert 'AI 判断' in overlay.result_text and overlay.result_hud_text.startswith('BACP_BLOCKS_V1')
    overlay.grab().save(str(Path(__file__).resolve().parents[3]/'work/shop-result-preview.png'))
    overlay.hide();overlay.deleteLater()


@pytest.mark.parametrize('changed',[False,True])
def test_worker_api_failure_fallback_and_stale_rejection(shop_state,monkeypatch,changed):
    from test_ui import qt
    from main import Worker
    from config.settings import Settings
    qt()
    states=[shop_state,shop_state.model_copy(deep=True)]
    if changed:states[1].model_extra['hud_fingerprint']='b'*64
    monkeypatch.setattr('main.StateRefresher.refresh',lambda _:states.pop(0))
    monkeypatch.setattr('main.DeepSeekClient.decide_shop',lambda *_:(_ for _ in ()).throw(CopilotError('连接失败')))
    w=Worker('fake',Settings());results=[];errors=[]
    w.result.connect(results.append);w.error.connect(errors.append);w.run()
    if changed:assert not results and errors and '已变化' in errors[0]
    else:assert results and not errors and not results[0]['calculation']['ai_reviewed'] and results[0]['calculation']['ai_error']=='连接失败'
