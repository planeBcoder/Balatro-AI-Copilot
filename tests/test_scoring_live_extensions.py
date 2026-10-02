import pytest
import json
from pathlib import Path
from test_game_hud import run_lua_contract
from test_strategy_v2 import finish_state
from core.scoring import score_hand

@pytest.mark.parametrize('key,ability,expected',[
 ('j_green_joker',{'mult':8,'extra':{'hand_add':1,'discard_sub':1}},150),
 ('j_ride_the_bus',{'mult':8,'extra':1},15), # Scoring King resets the bus.
 ('j_popcorn',{'mult':20,'extra':4},315),
 ('j_flash',{'mult':6,'extra':2},105),
 ('j_ice_cream',{'extra':{'chips':100,'chip_mod':5}},115),
 ('j_ramen',{'x_mult':2,'extra':.01},30),
 ('j_gros_michel',{'extra':{'mult':15,'odds':6}},240),
 ('j_cavendish',{'extra':{'Xmult':3,'odds':1000}},45),
 ('j_trousers',{'mult':6,'extra':2},105),
])
def test_exact_live_extension_current_hand(finish_state,key,ability,expected):
 finish_state.__pydantic_extra__['scoring_rules']['numeric_precision']='binary64'
 finish_state.jokers=[{'key':key,'ability':ability}]
 score=score_hand(finish_state,[0],'high_card')
 assert score.certified and score.value==expected

def test_flint_rounds_base_before_card_chips(finish_state):
 finish_state.blind.boss=True;finish_state.blind.key='bl_flint'
 # Base high card chips 5 ->3; mult1 ->1. King adds10 afterwards.
 assert score_hand(finish_state,[0],'high_card').value==13

def test_pillar_reads_real_debuff_flag(finish_state):
 finish_state.blind.boss=True;finish_state.blind.key='bl_pillar'
 finish_state.hand[0].debuffed=True
 assert score_hand(finish_state,[0],'high_card').value==5

@pytest.mark.parametrize('key,extra,code,expected',[
 ('j_greedy_joker',{'suit':'Spades','s_mult':3},'KS',60),
 ('j_smiley',5,'KS',90),('j_scary_face',30,'KS',45),
 ('j_photograph',2,'KS',30),('j_hanging_chad',2,'KS',35),
 ('j_hack',1,'5S',15),('j_fibonacci',8,'AS',144),
 ('j_even_steven',4,'TS',75),('j_odd_todd',31,'AS',47),
 ('j_scholar',{'chips':20,'mult':4},'AS',180),
 ('j_onyx_agate',7,'KC',120),('j_arrowhead',50,'KS',65),
])
def test_per_card_jokers(finish_state,key,extra,code,expected):
 finish_state.hand[0].code=code
 finish_state.jokers=[{'key':key,'ability':{'extra':extra}}]
 assert score_hand(finish_state,[0],'high_card').value==expected

def test_photograph_chad_retriggers_multiplier(finish_state):
 finish_state.jokers=[{'key':'j_photograph','ability':{'extra':2}},{'key':'j_hanging_chad','ability':{'extra':2}}]
 assert score_hand(finish_state,[0],'high_card').value==280

def test_arm_lowers_only_above_level_one(finish_state):
 finish_state.blind.boss=True;finish_state.blind.key='bl_arm'
 finish_state.poker_hands['High Card'].update(level=2,chips=15,mult=2)
 assert score_hand(finish_state,[0],'high_card').value==15

def test_shipped_card_individual_and_before_oracle():
 source=Path(__file__).resolve().parents[3]/'work/balatro-source/card.lua'
 contract=Path(__file__).with_name('vanilla_individual_contract.lua').read_text(encoding='utf-8')
 run_lua_contract(contract.replace('__CARD_SOURCE__',json.dumps(str(source).replace('\\','/'))))
