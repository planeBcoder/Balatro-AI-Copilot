-- Pure isolated game doubles: no real cards, key injection, API or save writes.
local files,plays,discards={},0,0
local t=100
local previous_calls=0
love={timer={getTime=function() return t end},data={hash=function(_,s)
    local n=0; for i=1,#s do n=(n+s:byte(i)*i)%251 end; return string.rep(string.char(n),32) end},
    filesystem={getInfo=function(path) return files[path] and {size=#files[path]} end,
        read=function(path) return files[path] end,
        write=function(path,text) assert(path=='balatro_copilot_auto_ack.txt');files[path]=text;return true end},
    update=function() previous_calls=previous_calls+1 end,keypressed=function() end}
BALATRO_COPILOT_HUD_V1={fingerprint=function() return tostring(G.GAME.current_round.hands_left)..':'..G.STATE end}
local function card(n) return {number=n,T={x=n},ability={},highlighted=false} end
G={STATE=7,STATES={SELECTING_HAND=7},GAME={round=1,round_resets={ante=1},blind={name='Small'},
    current_round={hands_left=4,discards_left=3},chips=0,STOP_USE=0},
    CONTROLLER={},play={cards={}},hand={cards={card(1),card(2),card(3)},highlighted={},config={highlighted_limit=5}},FUNCS={}}
function G.hand:unhighlight_all() for _,c in ipairs(self.cards) do c.highlighted=false end; self.highlighted={} end
function G.hand:add_to_highlighted(c) c.highlighted=true; self.highlighted[#self.highlighted+1]=c end
function G.FUNCS.can_play(e) e.config.button='play_cards_from_highlighted' end
function G.FUNCS.can_discard(e) e.config.button='discard_cards_from_highlighted' end
function G.FUNCS.play_cards_from_highlighted() plays=plays+1;G.GAME.current_round.hands_left=G.GAME.current_round.hands_left-1 end
function G.FUNCS.discard_cards_from_highlighted() discards=discards+1;G.GAME.current_round.discards_left=G.GAME.current_round.discards_left-1 end
math.random=function() error('Executor must not use game RNG') end
function pseudorandom() error('Executor must not use game RNG') end
dofile(__AUTO_SOURCE__)
local A=BALATRO_COPILOT_AUTO_V1
local run=string.rep('a',32)
local count=0
local function setlease(enabled,token,stamp)
    files['balatro_copilot_auto_lease.txt']=table.concat({'BACP_AUTO_LEASE_V1',token or run,stamp or os.time(),enabled and '1' or '0'},'\n')..'\n'
    love.update(.1)
end
local function command(action,edit)
    count=count+1
    local s=A.snapshot()
    local p={'BACP_AUTO_COMMAND_V1',string.format('%032x',count),os.time(),run,A.session,s.signature,action,tostring(s.card_ids[1])}
    for k,v in pairs(edit or {}) do p[k]=v end
    return table.concat(p,'\n')..'\n',p[2]
end
local function assert_reject(action,edits,reason)
    local raw,id=command(action,edits)
    local before=plays+discards
    A.execute(raw)
    assert(plays+discards==before,'Rejected command executed a game action')
    assert(files['balatro_copilot_auto_ack.txt']:find('|rejected|'..reason,1,true))
end
local raw,id=command('play')
A.execute(raw)
assert(plays==0 and files['balatro_copilot_auto_ack.txt']:find('no_lease',1,true))
setlease(true)
assert(A.snapshot().ready and A.active)
assert_reject('play',{[3]=os.time()-10},'expired')
assert_reject('play',{[5]=string.rep('b',64)},'session')
assert_reject('play',{[6]=string.rep('b',64)},'stale')
assert_reject('buy',nil,'action')
assert_reject('play',{[8]='1,,2'},'cards')
assert_reject('play',{[8]='1,1'},'cards')
assert_reject('play',{[8]='999'},'cards')
G.OVERLAY_MENU={}
assert_reject('play',nil,'not_ready')
G.OVERLAY_MENU=nil
G.CONTROLLER.locked=true
assert_reject('play',nil,'not_ready')
G.CONTROLLER.locked=nil
G.GAME.blind.block_play=true
assert_reject('play',nil,'not_ready')
G.GAME.blind.block_play=nil
G.CONTROLLER.dragging={target=G.hand.cards[1]}
assert_reject('play',nil,'not_ready')
G.CONTROLLER.dragging=nil
G.GAME.current_round.discards_left=0
assert_reject('discard',nil,'resources')
G.GAME.current_round.discards_left=3
G.hand.cards[2].ability.forced_selection=true
assert_reject('play',nil,'forced_selection')
G.hand.cards[2].ability.forced_selection=nil
-- Sorting the visible hand must invalidate an index-based old decision.
raw,id=command('play')
G.hand.cards[1],G.hand.cards[2]=G.hand.cards[2],G.hand.cards[1]
G.hand.cards[1].T.x,G.hand.cards[2].T.x=1,2
A.execute(raw)
assert(plays==0 and files['balatro_copilot_auto_ack.txt']:find('stale',1,true))
raw,id=command('play')
A.execute(raw)
assert(plays==1 and G.GAME.current_round.hands_left==3)
assert(G.hand.highlighted[1]==G.hand.cards[1])
assert(files['balatro_copilot_auto_ack.txt']:find('|accepted|invoked',1,true))
A.execute(raw)
assert(plays==1,'Duplicate action must not execute twice')
love.update(.1)
raw,id=command('discard')
A.execute(raw)
assert(discards==1 and G.GAME.current_round.discards_left==2)
love.update(.1)
love.keypressed('escape')
assert(not A.active and A.snapshot().cancelled_run==run)
assert_reject('play',nil,'no_lease')
setlease(true)
assert(not A.active,'Heartbeat must not undo user Escape cancellation')
run=string.rep('c',32)
setlease(true)
assert(A.active)
-- Button disabled after selection: restore old selection without playing.
local old_selected=G.hand.highlighted[1]
local original_allowed=G.FUNCS.can_play
G.FUNCS.can_play=function(e) e.config.button=nil end
assert_reject('play',nil,'blocked')
assert(not A.active and G.hand.highlighted[1]==old_selected)
G.FUNCS.can_play=original_allowed
run=string.rep('d',32)
setlease(true)
setlease(false)
assert_reject('play',nil,'no_lease')
setlease(true,nil,os.time()-8)
assert(not A.active)
assert(G.GAME.chips==0 and previous_calls>0)
print('Autoplay contract passed: lease, cancellation, stale state, physical IDs, duplicate prevention and normal callbacks')
