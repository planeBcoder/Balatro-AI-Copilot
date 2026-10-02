-- Full-run game-action bridge. No score, RNG, deck-order or save overrides.
-- Only standard callbacks; stopped unless an expiring explicit lease exists.
local A=assert(BALATRO_COPILOT_AUTO_V1)
local old_snapshot=A.snapshot
local ids=setmetatable({},{__mode='k'})
local next_id=0
local function id(c)
    if not ids[c] then next_id=next_id+1; ids[c]=next_id end
    return ids[c]
end
local function hash(s)
    return (love.data.hash('sha256',s):gsub('.',function(c)return string.format('%02x',c:byte())end))
end
local function copy(v,depth)
    if type(v)~='table' then return type(v)=='function' and nil or v end
    if depth<=0 then return nil end
    local out={}
    for k,x in pairs(v) do if type(k)=='string' or type(k)=='number' then out[k]=copy(x,depth-1) end end
    return out
end
local function cards(area)
    local out={}
    for _,c in ipairs(area and area.cards or {}) do
        out[#out+1]={id=id(c),key=c.config.center.key,name=c.ability.name,set=c.ability.set,
            cost=c.cost,sell_cost=c.sell_cost,ability=copy(c.ability,3),edition=copy(c.edition,1),
            debuffed=c.debuff,eternal=c.ability.eternal,facing=c.facing}
    end
    return out
end
local function button(root,name,seen)
    if type(root)~='table' then return nil end
    seen=seen or {}; if seen[root] then return nil end; seen[root]=true
    if root.config and root.config.button==name then return root end
    local found=button(root.UIRoot,name,seen)
    if found then return found end
    found=button(root.config and root.config.object,name,seen)
    if found then return found end
    for _,child in pairs(root.children or {}) do
        local found=button(child,name,seen)
        if found then return found end
    end
end
local function state_name()
    for k,v in pairs(G.STATES) do if v==G.STATE then return k end end
    return 'UNKNOWN'
end
local function active_button(root,name)
    local found=button(root,name)
    if found then return found end
    -- Cash-out lives in a separately attached UIBox, not round_eval.children.
    for _,box in pairs(G.I and G.I.UIBOX or {}) do
        found=button(box,name)
        if found then return found end
    end
end
function A.snapshot()
    local s=old_snapshot()
    if not G or not G.GAME then return s end
    local g=G.GAME
    local r={version=1,state=state_name(),won=g.won,win_notified=g.win_notified,
        win_overlay=G.OVERLAY_MENU and G.OVERLAY_MENU:get_UIE_by_ID('jimbo_spot')~=nil,
        seed=g.pseudorandom and g.pseudorandom.seed,seeded=g.seeded,stake=g.stake,
        ante=g.round_resets and g.round_resets.ante,blind_on_deck=g.blind_on_deck,
        blind_states=copy(g.round_resets and g.round_resets.blind_states,1),
        dollars=g.dollars,bankrupt_at=g.bankrupt_at,reroll_cost=g.current_round and g.current_round.reroll_cost,
        jokers=cards(G.jokers),shop=cards(G.shop_jokers),boosters=cards(G.shop_booster),
        vouchers=cards(G.shop_vouchers),pack=cards(G.pack_cards),consumables=cards(G.consumeables),
        joker_limit=G.jokers and G.jokers.config.card_limit,hand_facing={},
        locked=G.CONTROLLER and G.CONTROLLER.locked,stop_use=g.STOP_USE,
        locks=copy(G.CONTROLLER and G.CONTROLLER.locks,1),paused=G.SETTINGS.paused,
        overlay=G.OVERLAY_MENU~=nil,
        unlock_ready=G.OVERLAY_MENU and button(G.OVERLAY_MENU,'continue_unlock')~=nil or false,
        pending=false,lab=love.filesystem.getIdentity()=='BalatroCopilotLab'
            or love.filesystem.getIdentity()=='BalatroCopilotShopLab'}
    for i,c in ipairs(G.hand and G.hand.cards or {}) do r.hand_facing[i]=c.facing end
    -- Signature covers identity + ordered areas + balances + transient readiness.
    local fields={s.signature or '',r.state,tostring(r.dollars),tostring(r.reroll_cost),tostring(G.OVERLAY_MENU),tostring(r.locked),tostring(r.stop_use)}
    for _,area in ipairs({r.jokers,r.shop,r.boosters,r.vouchers,r.pack,r.consumables}) do
        for _,c in ipairs(area) do fields[#fields+1]=c.id..':'..c.key..':'..tostring(c.cost) end
        fields[#fields+1]=';'
    end
    r.signature=hash(table.concat(fields,'|'))
    local menu_ready=r.state=='MENU' or (r.state=='GAME_OVER' and G.OVERLAY_MENU~=nil)
    -- A paused loss screen intentionally leaves STOP_USE > 0. Restart is a
    -- normal menu action, not a consumable action; do not wait forever on it.
    r.ready=(not r.locked and ((g.STOP_USE or 0)<=0 or menu_ready) or r.unlock_ready) and not A.run_pending
    r.cash_ready=G.round_eval and active_button(G.round_eval,'cash_out')~=nil or false
    r.blind_ready=G.blind_select~=nil and button(G.blind_select,'select_blind')~=nil
    r.shop_ready=G.shop~=nil and button(G.shop,'toggle_shop')~=nil
    s.fullrun=r
    return s
end
local function lines(raw)
    if type(raw)~='string' or #raw>900 or raw:find('\r',1,true) then return nil end
    local p={}; for v in raw:gmatch('([^\n]*)\n') do p[#p+1]=v end
    return p
end
local function hex(v,n) return type(v)=='string' and #v==n and not v:find('[^a-f0-9]') end
local function leased(token)
    local p=lines(love.filesystem.read('balatro_copilot_auto_lease.txt'))
    local stamp=p and tonumber(p[3])
    return p and #p==4 and p[1]=='BACP_AUTO_LEASE_V1' and p[2]==token and p[4]=='1'
        and stamp and os.time()-stamp>=0 and os.time()-stamp<=3 and A.cancelled~=token
end
local function ack(request,status,reason)
    love.filesystem.write('balatro_copilot_run_ack.txt',table.concat({'BACP_RUN_ACK_V1',request,status,reason},'|')..'\n')
end
local seen={}
local function resolve(area,target)
    for _,c in ipairs(area and area.cards or {}) do if tostring(id(c))==target then return c end end
end
local function invoke(p)
    local request,token,action,target=p[2],p[4],p[7],p[8]
    if seen[request] then ack(request,'rejected','duplicate'); return end
    seen[request]=true
    local stamp=tonumber(p[3])
    if not stamp or os.time()-stamp<0 or os.time()-stamp>3 or not leased(token) then ack(request,'rejected','lease');return end
    local s=A.snapshot(); local r=s.fullrun
    if p[5]~=A.session or not hex(p[6],64) or p[6]~=r.signature or not r.ready then ack(request,'rejected','stale');return end
    local e={config={}}
    local f=nil
    if action=='unlock' and r.unlock_ready then
        e=button(G.OVERLAY_MENU,'continue_unlock');f=function()G.FUNCS.continue_unlock(e)end
    elseif action=='start' and r.lab and (r.state=='MENU' or r.state=='GAME_OVER') then
        f=function()
            if G.OVERLAY_MENU then G.FUNCS.exit_overlay_menu() end
            if G.FUNCS.change_gamespeed then G.FUNCS.change_gamespeed({to_val=4}) end
            local saved=G.SAVED_GAME
            if r.state=='MENU' and not saved then
                local data=get_compressed(G.SETTINGS.profile..'/save.jkr')
                if data then saved=STR_UNPACK(data) end
            end
            if not saved and G.P_CENTERS and G.P_CENTERS.b_yellow and G.P_CENTERS.b_yellow.unlocked then
                for i,c in ipairs(G.P_CENTER_POOLS.Back) do
                    if c.key=='b_yellow' then G.FUNCS.change_selected_back({to_key=i});break end
                end
            end
            G.FUNCS.start_run(nil,saved and {savetext=saved} or {stake=1})
        end
    elseif action=='blind' and r.state=='BLIND_SELECT' and r.blind_ready then
        e=button(G.blind_select,'select_blind'); f=function()G.FUNCS.select_blind(e)end
    elseif action=='cash' and r.state=='ROUND_EVAL' and r.cash_ready and not G.OVERLAY_MENU then
        e=active_button(G.round_eval,'cash_out'); f=function()G.FUNCS.cash_out(e)end
    elseif action=='leave' and r.state=='SHOP' and r.shop_ready then
        e=button(G.shop,'toggle_shop'); f=function()G.FUNCS.toggle_shop(e)end
    elseif action=='reroll' and r.state=='SHOP' and r.shop_ready then
        -- The enabled game button is required; no invented free rerolls.
        e=button(G.shop,'reroll_shop')
        if e and G.GAME.current_round.reroll_cost<=G.GAME.dollars-G.GAME.bankrupt_at then f=function()G.FUNCS.reroll_shop(e)end end
    elseif (action=='buy' or action=='open') and r.state=='SHOP' and r.shop_ready then
        local c=resolve(action=='buy' and G.shop_jokers or G.shop_booster,target)
        if c and c.cost<=G.GAME.dollars-G.GAME.bankrupt_at then
            e.config.ref_table=c
            if action=='buy' and (c.ability.set=='Joker' or c.ability.set=='Planet') and G.FUNCS.check_for_buy_space(c) then
                f=function()G.FUNCS.buy_from_shop(e)end
            elseif action=='open' and c.ability.set=='Booster' then f=function()G.FUNCS.use_card(e)end end
        end
    elseif action=='sell' and r.state=='SHOP' and r.shop_ready then
        local c=resolve(G.jokers,target)
        if c and not c.ability.eternal then e.config.ref_table=c;f=function()G.FUNCS.sell_card(e)end end
    elseif (action=='front' or action=='last') and r.state=='SHOP' and r.shop_ready then
        local c=resolve(G.jokers,target)
        if c and not c.ability.pinned and c.facing=='front' then
            -- Use the game's own drag-position ordering and align routine.
            -- No ownership, ability, score, card creation or RNG changes.
            f=function()
                c.states.drag.is=true
                local edge=c.T.x
                for _,j in ipairs(G.jokers.cards) do
                    if action=='front' then edge=math.min(edge,j.T.x) else edge=math.max(edge,j.T.x) end
                end
                c.T.x=edge+(action=='front' and -2 or 2)
                G.jokers:align_cards()
                c.states.drag.is=false
                G.jokers:align_cards()
            end
        end
    elseif action=='use' and (r.state=='SHOP' or r.state=='BLIND_SELECT') then
        local c=resolve(G.consumeables,target)
        if c and c.ability.set=='Planet' and c:can_use_consumeable() then e.config.ref_table=c;f=function()G.FUNCS.use_card(e)end end
    elseif action=='take' and (r.state=='BUFFOON_PACK' or r.state=='PLANET_PACK') then
        local c=resolve(G.pack_cards,target)
        if c and ((c.ability.set=='Joker' and G.FUNCS.check_for_buy_space(c)) or (c.ability.set=='Planet' and c:can_use_consumeable())) then
            e.config.ref_table=c; f=function()G.FUNCS.use_card(e)end
        end
    elseif action=='skip' and r.state:find('_PACK',1,true) and G.booster_pack then
        f=function()G.FUNCS.skip_booster(e)end
    end
    if not f then ack(request,'rejected','blocked'); return end
    if not leased(token) then ack(request,'rejected','cancelled');return end
    -- Suppress duplicates during callback/animation, without touching game locks.
    A.run_pending=love.timer.getTime()+1.0
    local ok=pcall(f)
    if not ok then A.cancel(); ack(request,'rejected','outcome_unknown');return end
    ack(request,'accepted','invoked')
end
local old_update=love.update
local last=love.filesystem.read('balatro_copilot_run_command.txt')
local elapsed=0
function love.update(dt)
    if old_update then old_update(dt) end
    if A.run_pending and love.timer.getTime()>A.run_pending then A.run_pending=nil end
    elapsed=elapsed+dt; if elapsed<0.08 then return end; elapsed=0
    local info=love.filesystem.getInfo('balatro_copilot_run_command.txt')
    if not info or info.size>900 then return end
    local raw=love.filesystem.read('balatro_copilot_run_command.txt')
    if raw==last then return end; last=raw
    local p=lines(raw)
    if p and #p==8 and p[1]=='BACP_RUN_COMMAND_V1' and hex(p[2],32) then
        local ok=pcall(invoke,p)
        if not ok then A.cancel();ack(p[2],'rejected','error') end
    end
end
-- Evidence capture is a passive game feature, never a source for mouse actions.
local old_draw=love.draw
local victory_captured=false
local victory_capture_at=nil
function love.draw()
    if old_draw then old_draw() end
    if G and G.GAME and G.GAME.win_notified and G.GAME.won and G.OVERLAY_MENU and not victory_captured then
        victory_capture_at= victory_capture_at or love.timer.getTime()+2
        if love.timer.getTime()>=victory_capture_at then
            victory_captured=true
            love.graphics.captureScreenshot('copilot-victory.png')
        end
    end
end
