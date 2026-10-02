from pathlib import Path
import json
import pytest
from core.fullrun import planned_candidate,RunTransport,best_score,annotate_general_draws,run_metadata,hidden_choice,FullRunTrainer,planet_value
from core.state_schema import parse_state
from core.calculation import candidates
from core.errors import CopilotError
from test_game_hud import run_lua_contract,ASSETS

@pytest.fixture
def state():
    s=parse_state((Path(__file__).parent/'fixtures/observed_finish_state.json').read_bytes())
    s.__pydantic_extra__.update(scoring_rules={'version':2,'back_key':'b_red','modifiers':{},'other_mods':[]},autoplay={'version':1,'session':'a'*64,'fullrun':{'lab':True,'ready':True,'signature':'b'*64,'hand_facing':['front']*len(s.hand)}})
    return s

def test_certified_finish_always_wins_over_attractive_discard(state):
    calc=candidates(state,500)
    calc['candidates'].append({'id':'d99','action':'discard','next_play':{'expected_best_score':10**9}})
    assert planned_candidate(calc,4)['id'] in calc['immediate_finish_ids']

def test_draw_requires_material_gain():
    play={'id':'p','action':'play','expected_score':100,'score_certified':True}
    discard={'id':'d','action':'discard','next_play':{'expected_best_score':110}}
    calc={'candidates':[play,discard]}
    assert planned_candidate(calc,4)==play
    discard['next_play']['expected_best_score']=150
    assert planned_candidate(calc,4)==discard

def test_last_hand_has_lower_gain_threshold():
    p={'id':'p','action':'play','expected_score':100,'score_certified':True}
    d={'id':'d','action':'discard','next_play':{'expected_best_score':125}}
    calc={'candidates':[p,d]}
    assert planned_candidate(calc,1)==d and planned_candidate(calc,4)==p

def test_transport_default_off_and_no_menu_action_without_lease(tmp_path,state):
    t=RunTransport(tmp_path)
    with pytest.raises(CopilotError):t.execute_run(state,'start')
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('action,target',[('eval',''),('buy','1\nquit'),('buy','01')])
def test_transport_rejects_untyped_inputs(tmp_path,state,action,target):
    t=RunTransport(tmp_path);t.start()
    with pytest.raises(CopilotError):t.execute_run(state,action,target)
    assert not (tmp_path/'balatro_copilot_run_command.txt').exists()
    t.stop()

def test_actual_card_indices_and_order_used(state):
    score,indices=best_score(state)
    assert score>0 and list(indices)==sorted(indices)
    assert len(set(indices))==len(indices)

def test_face_down_disables_draw_planning(state):
    state.__pydantic_extra__['autoplay']['fullrun']['hand_facing'][0]='back'
    calc={'candidates':[{'action':'discard','keep_indices':[0],'draws':1}]}
    annotate_general_draws(state,calc)
    assert 'next_play' not in calc['candidates'][0]


def test_general_draw_does_not_overwrite_higher_sample_plan(state):
    precise = {'sample_count':96,'expected_best_score':120}
    calc = {'candidates':[{'action':'discard','next_play':precise}]}
    annotate_general_draws(state,calc)
    assert calc['candidates'][0]['next_play'] is precise


def test_hidden_strategy_does_not_depend_on_unseen_ranks(state):
    state.blind.target='1000000'
    facing=state.__pydantic_extra__['autoplay']['fullrun']['hand_facing']
    facing[0]='back'
    state.discards_left=3
    first=hidden_choice(state)
    state.hand[0].code='2D' if state.hand[0].code!='2D' else 'AS'
    second=hidden_choice(state)
    assert first['action']==second['action']=='discard'
    assert first['indices']==second['indices']==[0]


def test_hidden_strategy_plays_visible_cards_without_discards(state):
    state.__pydantic_extra__['autoplay']['fullrun']['hand_facing'][0]='back'
    state.discards_left=0
    choice=hidden_choice(state)
    assert choice['action']=='play' and 0 not in choice['indices']
    assert choice['score_certified'] is False


def test_shop_moves_individual_mult_before_photo(state,tmp_path):
    r=run_metadata(state)
    r.update(jokers=[{'key':'j_photograph','id':1},{'key':'j_smiley','id':2}],dollars=0,joker_limit=5,consumables=[])
    runner=FullRunTrainer(tmp_path,lambda:state,None,tmp_path/'log')
    try:assert runner.shop_choice(state)==('front',2)
    finally:runner.events.close()


def test_planet_value_considers_actual_build(state):
    run_metadata(state)['jokers']=[]
    state.poker_hands['High Card'].update(chips=200,mult=40,level=20)
    strong={'ability':{'consumeable':{'hand_type':'High Card'}}}
    fancy={'ability':{'consumeable':{'hand_type':'Straight Flush'}}}
    assert planet_value(state,strong)>planet_value(state,fancy)

def test_fullrun_lua_parser_and_actual_ui_box_contract():
    contract=(Path(__file__).parent/'fullrun_contract.lua').read_text(encoding='utf-8')
    run_lua_contract(contract.replace('__RUN_SOURCE__',json.dumps(str(ASSETS/'fullrun/main.lua').replace('\\','/'))))
