from pathlib import Path
import pytest
from core.hud_install import precision_exporter_text, upgrade_numeric_precision
from test_game_hud import run_lua_contract
from test_strategy_v2 import finish_state
from core.scoring import score_hand


ENCODER='''local function json_encode(value, indent)
    local kind = type(value)
    if kind == "number" then
        return tostring(value)
    end
    if kind == "string" then return value end
end
local function export()
        state.request_id = token
        state.scoring_rules = {version = 2,
            back_key = "b_red"}
end
'''


def test_binary64_round_trip_uses_actual_lua_number():
    updated=precision_exporter_text(ENCODER)
    run_lua_contract(updated+'''
local x=2
for i=1,35 do x=x-.01 end
assert(tonumber(json_encode(x))==x)
assert(tonumber(tostring(x))~=x)
''')
    assert 'numeric_precision = "binary64"' in updated
    assert precision_exporter_text(updated)==updated


def test_precision_upgrade_keeps_backup_and_is_idempotent(tmp_path):
    path=tmp_path/'Mods/BalatroStateExporter/main.lua'
    path.parent.mkdir(parents=True)
    path.write_text(ENCODER)
    assert upgrade_numeric_precision(tmp_path)=='installed'
    assert path.with_name('main.lua.pre-binary64.bak').read_text()==ENCODER
    assert upgrade_numeric_precision(tmp_path)=='ready'


def test_unknown_encoder_not_overwritten():
    with pytest.raises(OSError):precision_exporter_text('unknown script')


def test_mutable_fraction_without_roundtrip_not_certified(finish_state):
    finish_state.jokers=[{'key':'j_ramen','ability':{'x_mult':1.65,'extra':.01}}]
    assert not score_hand(finish_state,[0],'high_card').certified
    finish_state.__pydantic_extra__['scoring_rules']['numeric_precision']='binary64'
    assert score_hand(finish_state,[0],'high_card').certified
