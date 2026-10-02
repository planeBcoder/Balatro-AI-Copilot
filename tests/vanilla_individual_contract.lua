-- Shipped real Card methods, isolated objects. No live game, no RNG.
Moveable={extend=function()return {}end}
dofile(__CARD_SOURCE__)
function localize()return ''end
function pseudorandom()error('No game RNG allowed in deterministic scoring tests')end
G={C={RED={},CHIPS={},MULT={}},GAME={current_round={},hands={}},play={},jokers={},hand={cards={}}}
local c={base={id=13,suit='Spades'},ability={}}
function c:is_face()return self.base.id>=11 and self.base.id<=13 end
function c:get_id()return self.base.id end
function c:is_suit(s)return self.base.suit==s end
local function effect(name,extra,context,edits)
 local a={name=name,set='Joker',extra=extra,mult=0,x_mult=1,t_mult=0,t_chips=0}
 for k,v in pairs(edits or {})do a[k]=v end
 local j=setmetatable({ability=a},{__index=Card})
 local ctx={individual=true,cardarea=G.play,other_card=c,scoring_hand={c},full_hand={c},poker_hands={Pair={{}},['Two Pair']={{}}}}
 for k,v in pairs(context or {})do ctx[k]=v end
 return j:calculate_joker(ctx),j
end
assert(effect('Smiley Face',5).mult==5)
assert(effect('Scary Face',30).chips==30)
assert(effect('Photograph',2).x_mult==2)
assert(effect('Greedy Joker',{suit='Spades',s_mult=3},nil,{effect='Suit Mult'}).mult==3)
c.base.id=14
assert(effect('Fibonacci',8).mult==8)
assert(effect('Scholar',{chips=20,mult=4}).chips==20)
assert(effect('Odd Todd',31).chips==31)
c.base.id=10
assert(effect('Even Steven',4).mult==4)
c.base.id=13
assert(effect('Hanging Chad',2,{individual=false,repetition=true}).repetitions==2)
assert(effect('Sock and Buskin',1,{individual=false,repetition=true}).repetitions==1)
c.base.id=5
assert(effect('Hack',1,{individual=false,repetition=true}).repetitions==1)
local _,green=effect('Green Joker',{hand_add=1,discard_sub=1},{individual=false,before=true,cardarea=G.jokers},{mult=8})
assert(green.ability.mult==9)
local _,pants=effect('Spare Trousers',2,{individual=false,before=true,cardarea=G.jokers},{mult=6})
assert(pants.ability.mult==8)
print('Expanded deterministic Joker oracle passed')
