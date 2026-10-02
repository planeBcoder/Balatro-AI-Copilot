-- Isolated exporter VM: asserts no scoring, RNG, save write or game mutation.
local files = {}
local token = string.rep('a',32)
SMODS={current_mod={id='balatro_state_exporter'},Mods={
    Steamodded={can_load=true},Lovely={can_load=true},Balatro={can_load=true},
    balatro_state_exporter={can_load=true},balatro_copilot_hud={can_load=true},
    disabled_example={can_load=false}}}
love={filesystem={read=function(path) return files[path] end,
    write=function(path,text) assert(path:find('balatro_',1,true)); files[path]=text; return true end},
    system={setClipboardText=function() error('Request bridge must not use clipboard') end}}
G={VERSION='1.0.1o-FULL',STATE=7,STATES={SELECTING_HAND=7},
    GAME={chips=0,dollars=4,round=1,round_resets={ante=1},
        current_round={hands_left=4,discards_left=4},
        selected_back={effect={center={key='b_red'}}},modifiers={},
        blind={name='Small Blind',chips=300,config={blind={key='bl_small'}}},
        hands={['High Card']={level=1,chips=5,mult=1,played=0}}},
    hand={cards={{base={id=13,value='King',suit='Spades'},ability={bonus=0,perma_bonus=0},config={center={key='c_base'}}}}},
    deck={cards={}},jokers={cards={}},consumeables={cards={}}}
function pseudorandom() error('Read-only export must not use RNG') end
dofile(__EXPORTER_SOURCE__)
files['balatro_copilot_request.txt']=token..'|'..os.time()
love.update(0.1)
local response=files['balatro_copilot_response.json']
assert(response and response:find('"scoring_rules"',1,true))
assert(response:find('"back_key": "b_red"',1,true))
assert(response:find('"other_mods": []',1,true),'Framework metadata must not look like unsupported gameplay mods')
assert(response:find('"request_id": "'..token..'"',1,true))
assert(G.GAME.chips==0 and G.GAME.current_round.hands_left==4 and #G.hand.cards==1)
assert(not files['balatro_state.json'],'Nonce export must not trigger F8 clipboard path')
print('Read-only scoring metadata contract passed')
