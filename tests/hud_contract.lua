-- Real Lua 5.1 VM, isolated graphics/file mocks, no active-game input.
local writes, stack, previous_calls, mouse_calls = {}, 0, 0, 0
local raw, clock, render_fail = nil, 1, false
local function newfont(_,size)
    return {size=size,getHeight=function() return size end,
        getWrap=function(_,text,width) return width,{text} end}
end
local graphics = {getDimensions=function() return 1920,1080 end,
    newFont=newfont,
    push=function() stack=stack+1 end, pop=function() stack=stack-1 end,
    rectangle=function() if render_fail then error('injected draw failure') end end,
    print=function() end, circle=function() end}
for _,name in ipairs({'setCanvas','origin','setShader','setScissor','setStencilTest','setBlendMode','setColor','setLineWidth','setFont'}) do graphics[name]=function() end end
love = {graphics=graphics, timer={getTime=function() return clock end},
    mouse={getPosition=function() return 100,100 end},
    data={hash=function(algorithm,s) assert(algorithm=='sha256'); local n=0; for i=1,#s do n=(n+s:byte(i))%250 end; return string.rep(string.char(n),32) end},
    filesystem={getInfo=function() return raw and {size=#raw} end, read=function() return raw end,
        write=function(path,value) writes[path]=value; return true end},
    draw=function() previous_calls=previous_calls+1 end, update=function() end,
    mousepressed=function() mouse_calls=mouse_calls+1 end,
    mousereleased=function() mouse_calls=mouse_calls+1 end,
    mousemoved=function() end, wheelmoved=function() end}
G={STATE=7,GAME={chips=0,round=1,dollars=4,current_round={hands_left=4,discards_left=4},
    blind={name='Small',chips=300},hands={},pseudorandom={seed='TEST'}},
    hand={cards={{base={id=14,suit='Hearts'},ability={},config={center={key='c_base'}}}}},
    deck={cards={}},jokers={cards={}}}
math.random=function() error('HUD must not use game RNG') end
math.randomseed=function() error('HUD must not seed game RNG') end
dofile(__HUD_SOURCE__)
local H=BALATRO_COPILOT_HUD_V1
local signature=H.fingerprint()
local function payload(seq,kind,fp,text,stamp)
    return table.concat({'BACP_HUD_V1',stamp or os.time(),seq,'1',kind,fp or '',text or 'Hello'},'\n')..'\n'
end
assert(H.parse(payload(1,'idle')))
assert(not H.parse(payload(1,'idle','','bad%QQ')))
assert(not H.parse(payload(1,'idle','','bad%')))
assert(not H.parse(payload(1,'idle','','%00')))
assert(not H.parse(payload(1,'idle','','valid',os.time()-30)))
assert(not H.parse(payload(1,'result','bad')))
assert(not H.parse(payload(-1,'idle')))
assert(not H.parse(payload(1,'execute')))
raw=payload(1,'result',signature,'Advice%0Asecond%20line')
love.update(0.3)
love.draw()
assert(H.message.text=='Advice\nsecond line' and not H.stale)
assert(stack==0 and previous_calls==1 and H.draws==1)
assert(H.fingerprint()==signature, 'Drawing must not change game state')
assert(writes['balatro_copilot_hud_ready.txt']:find('|ok',1,true))
love.mousepressed(H.x+10,H.y+60,1)
love.mousereleased(H.x+10,H.y+60,1)
assert(mouse_calls==0,'Panel click must not reach cards')
love.mousepressed(10,10,1)
love.mousereleased(10,10,1)
assert(mouse_calls==2,'Outside input must remain intact')
love.mousepressed(H.x+10,H.y+10,1)
love.mousemoved(H.x+50,H.y+30)
love.mousereleased(10,10,1)
assert(not H.drag and H.x>=0)
G.GAME.current_round.discards_left=3
love.update(0.3)
assert(H.stale,'Changed state must invalidate suggestion')
G.GAME.current_round.discards_left=4
love.update(0.3)
assert(H.stale,'Stale recommendation must not silently reactivate')
raw=payload(2,'result',H.fingerprint(),'fresh')
love.update(0.3)
assert(not H.stale)
-- Structured result must be parsed into typographic layers, with no text eval.
raw=payload(3,'result',H.fingerprint(),'BACP_BLOCKS_V1%0AM%09context%0AA%09play%0AP%09score%0AR%09reason%0AF%09footer')
love.update(0.3)
love.draw()
assert(#H.blocks==5)
assert(H.blocks[2].font.size > H.blocks[4].font.size)
assert(H.blocks[4].font.size > H.blocks[5].font.size)
assert(H.blocks[3].y > H.blocks[2].y+H.blocks[2].height)
assert(H.content_height > 0 and stack==0)
raw=payload(3,'stopped')
love.update(0.3)
love.draw()
assert(previous_calls==3 and stack==0)
raw=payload(4,'idle')
love.update(0.3)
render_fail=true
love.draw()
assert(H.failed and stack==0,'Render failures must restore graphics stack')
print('HUD Lua contract passed: parsing, drawing, input isolation, stale checks, failure recovery')
