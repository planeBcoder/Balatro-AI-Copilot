-- Explicit opt-in executor, separate from the read-only state exporter/HUD.
-- Only normal selection + play/discard callbacks. No scoring or RNG override.
if rawget(_G,"BALATRO_COPILOT_AUTO_V1") then return end
local A = {ids=setmetatable({},{__mode="k"}), next_id=0, seen={}, seen_order={}, active=false}
_G.BALATRO_COPILOT_AUTO_V1 = A
local LEASE,COMMAND,ACK = "balatro_copilot_auto_lease.txt","balatro_copilot_auto_command.txt","balatro_copilot_auto_ack.txt"
local function hash(s)
    return (love.data.hash("sha256",s):gsub(".",function(c) return string.format("%02x",c:byte()) end))
end
A.session = hash(tostring(A)..os.time()..love.timer.getTime()) -- Never uses game RNG.
local function hex(s,n) return type(s)=="string" and #s==n and not s:find("[^a-f0-9]") end
local function lines(raw,limit)
    if type(raw)~="string" or #raw>limit or raw:find("\r",1,true) then return nil end
    local out={}
    for s in raw:gmatch("([^\n]*)\n") do out[#out+1]=s end
    return out
end
local function read(path,limit)
    local info=love.filesystem.getInfo(path)
    if not info or info.size>limit then return nil end
    return love.filesystem.read(path)
end
local function lease()
    local p=lines(read(LEASE,180),180)
    if not p or #p~=4 or p[1]~="BACP_AUTO_LEASE_V1" or not hex(p[2],32) then return nil end
    local stamp=tonumber(p[3])
    if p[4]~="1" or not stamp or os.time()-stamp<0 or os.time()-stamp>3 or A.cancelled==p[2] then return nil end
    return p[2]
end
local function ready()
    if not G or not G.GAME or not G.hand or not G.CONTROLLER or G.OVERLAY_MENU then return false end
    if G.STATE~=G.STATES.SELECTING_HAND or #G.hand.cards==0 or G.CONTROLLER.locked then return false end
    if G.GAME.blind and G.GAME.blind.block_play then return false end
    if G.CONTROLLER.dragging and G.CONTROLLER.dragging.target then return false end
    if G.play and #G.play.cards>0 then return false end
    if (G.GAME.STOP_USE or 0)>0 or A.pending then return false end
    if not rawget(_G,"BALATRO_COPILOT_HUD_V1") then return false end
    return true
end
function A.snapshot()
    if not G or not G.GAME or not G.hand then return {version=1,ready=false,session=A.session} end
    local ids,ordered,forced={},{},false
    local aligned,last_x=true,-math.huge
    for i,c in ipairs(G.hand.cards) do
        if not A.ids[c] then A.next_id=A.next_id+1; A.ids[c]=A.next_id end
        ids[i]=A.ids[c]
        ordered[i]=ids[i]..":"..tostring(c.highlighted)..":"..tostring(c.ability and c.ability.forced_selection)
        -- Normal callbacks score in T.x order. Refuse while the hand's array
        -- differs from its visual order, rather than certify a reordered score.
        local x=c.T and c.T.x
        if type(x)~="number" or x~=x or x<last_x or (c.states and c.states.drag and c.states.drag.is) then aligned=false end
        if type(x)=="number" then last_x=x end
        if c.ability and c.ability.forced_selection then forced=true end
    end
    local hud=rawget(_G,"BALATRO_COPILOT_HUD_V1")
    local fp=hud and hud.fingerprint() or "unavailable"
    local blind=G.GAME.blind or {}
    return {version=1,session=A.session,signature=hash(fp..table.concat(ordered,",")),card_ids=ids,
        ready=ready() and aligned,active=A.active,cancelled_run=A.cancelled,forced_selection=forced,
        blind_id=tostring(G.GAME)..":"..tostring(G.GAME.round)..":"..tostring(G.GAME.round_resets and G.GAME.round_resets.ante)..":"..tostring(blind.name)}
end
function A.cancel()
    A.cancelled=A.run
    A.active=false
end
local function ack(id,status,reason)
    love.filesystem.write(ACK,table.concat({"BACP_AUTO_ACK_V1",id,status,reason},"|").."\n")
end
local function execute(raw)
    local p=lines(raw,900)
    if not p or #p~=8 or p[1]~="BACP_AUTO_COMMAND_V1" or not hex(p[2],32) then return end
    local id=p[2]
    if A.seen[id] then ack(id,"rejected","duplicate"); return end
    A.seen[id]=true; A.seen_order[#A.seen_order+1]=id
    if #A.seen_order>64 then A.seen[table.remove(A.seen_order,1)]=nil end
    local stamp=tonumber(p[3])
    if not stamp or os.time()-stamp<0 or os.time()-stamp>3 then ack(id,"rejected","expired"); return end
    local run=lease()
    if not run or p[4]~=run then ack(id,"rejected","no_lease"); return end
    if p[5]~=A.session or not hex(p[6],64) then ack(id,"rejected","session"); return end
    if p[7]~="play" and p[7]~="discard" then ack(id,"rejected","action"); return end
    if not ready() then ack(id,"rejected","not_ready"); return end
    local snapshot=A.snapshot()
    if not snapshot.ready then ack(id,"rejected","not_ready"); return end
    if snapshot.signature~=p[6] then ack(id,"rejected","stale"); return end
    if snapshot.forced_selection then ack(id,"rejected","forced_selection"); return end
    local chosen,used,labels={},{},{}
    if p[8]:find("[^%d,]") then ack(id,"rejected","cards"); return end
    for value in p[8]:gmatch("[^,]+") do
        local card_id=tonumber(value)
        if not card_id or value~=tostring(card_id) or used[card_id] then ack(id,"rejected","cards"); return end
        labels[#labels+1]=value
        used[card_id]=true
        local card=nil
        for _,c in ipairs(G.hand.cards) do if A.ids[c]==card_id then card=c; break end end
        if not card then ack(id,"rejected","cards"); return end
        chosen[#chosen+1]=card
    end
    if table.concat(labels,",")~=p[8] then ack(id,"rejected","cards"); return end
    if #chosen<1 or #chosen>5 or #chosen>(G.hand.config.highlighted_limit or 5) then ack(id,"rejected","cards"); return end
    local round=G.GAME.current_round or {}
    if (p[7]=="play" and ((round.hands_left or 0)<=0 or G.GAME.blind.block_play)) or
       (p[7]=="discard" and (round.discards_left or 0)<=0) then ack(id,"rejected","resources"); return end
    local previous={}
    for _,c in ipairs(G.hand.highlighted) do previous[#previous+1]=c end
    local function restore()
        G.hand:unhighlight_all()
        for _,c in ipairs(previous) do G.hand:add_to_highlighted(c,true) end
    end
    local invoked=false
    local ok=pcall(function()
        G.hand:unhighlight_all()
        for _,c in ipairs(chosen) do G.hand:add_to_highlighted(c,true) end
        if #G.hand.highlighted~=#chosen then error("selection") end
        for _,c in ipairs(G.hand.highlighted) do if not used[A.ids[c]] then error("selection") end end
        local button={config={}}
        local allowed=p[7]=="play" and G.FUNCS.can_play or G.FUNCS.can_discard
        allowed(button)
        local callback=p[7]=="play" and "play_cards_from_highlighted" or "discard_cards_from_highlighted"
        if button.config.button~=callback then error("blocked") end
        -- Last cancellation check, before the standard game's callback.
        if lease()~=run then error("cancelled") end
        A.pending={hands=round.hands_left,discards=round.discards_left}
        invoked=true
        G.FUNCS[callback](button)
    end)
    if not ok then
        if not invoked then pcall(restore) end
        A.cancel() -- Never blindly retry a callback with unknown outcome.
        ack(id,"rejected",invoked and "outcome_unknown" or "blocked")
        return
    end
    ack(id,"accepted","invoked") -- Not a claim that scoring/animation completed.
end
A.execute=execute -- Isolated contract tests only; no executable IPC payloads.
local previous_update=love.update
local last=read(COMMAND,900)
local elapsed=0
function love.update(dt)
    if previous_update then previous_update(dt) end
    elapsed=elapsed+dt
    if elapsed<0.05 then return end
    elapsed=0
    local round=G and G.GAME and G.GAME.current_round
    if A.pending and round and (round.hands_left~=A.pending.hands or round.discards_left~=A.pending.discards) then A.pending=nil end
    local run=lease()
    if run and run~=A.run then A.cancelled=nil end
    A.run,A.active=run,run~=nil
    local raw=read(COMMAND,900)
    if raw and raw~=last then
        last=raw
        local ok=pcall(execute,raw)
        if not ok then A.cancel(); print('[Copilot Auto] Executor disabled after an error') end
    end
end
local previous_keypressed=love.keypressed
function love.keypressed(key,...)
    if key=="escape" and A.active then A.cancel() end
    if previous_keypressed then return previous_keypressed(key,...) end
end
print('[Copilot Auto] Opt-in current-Blind executor loaded. Escape cancels; no automatic startup.')
