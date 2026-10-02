-- Run shipped vanilla Card methods in an isolated VM, not the active game.
Moveable = {extend=function() return {} end}
dofile(__CARD_SOURCE__)
function localize() return '' end
function pseudorandom() error('Deterministic scorer must not need random results') end
G={C={RED={},CHIPS={},MULT={}},GAME={current_round={hands_left=0,discards_left=0},
    hands={['High Card']={played=3}}},play={cards={{},{}}},jokers={cards={{ability={set='Joker'}},{ability={set='Joker'}}}},hand={cards={}},deck={cards={{},{},{}}}}
local function effect(name, extra, edits)
    local ability={name=name,set='Joker',x_mult=1,t_mult=0,t_chips=0,type='',mult=4,extra=extra}
    for k,v in pairs(edits or {}) do ability[k]=v end
    local card=setmetatable({ability=ability},{__index=Card})
    return card:calculate_joker({joker_main=true,cardarea=G.jokers,full_hand=G.play.cards,
        scoring_name='High Card',scoring_hand=G.play.cards,poker_hands={Pair={{}},['High Card']={{}}}})
end
assert(effect('Joker').mult_mod==4)
assert(effect('Half Joker',{size=3,mult=20}).mult_mod==20)
assert(effect('Mystic Summit',{d_remaining=0,mult=15}).mult_mod==15)
assert(effect('Abstract Joker',3).mult_mod==6)
assert(effect('Acrobat',3).Xmult_mod==3)
assert(effect('Supernova').mult_mod==3)
assert(effect('Jolly Joker',nil,{type='Pair',t_mult=8}).mult_mod==8)
assert(effect('Blue Joker',2).chip_mod==6)
assert(effect('Egg',3)==nil)
local c=setmetatable({base={nominal=10},ability={bonus=30,perma_bonus=7,mult=4,x_mult=2,h_x_mult=1.5}},{__index=Card})
assert(c:get_chip_bonus()==47)
assert(c:get_chip_mult()==4)
assert(c:get_chip_x_mult()==2)
assert(c:get_chip_h_x_mult()==1.5)
c.debuff=true
assert(c:get_chip_bonus()==0 and c:get_chip_mult()==0)
print('Vanilla scoring oracle passed')
