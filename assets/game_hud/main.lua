-- Display-only HUD. Never execute IPC text; never touch scores, RNG or saves.
if rawget(_G, "BALATRO_COPILOT_HUD_V1") then return end
local H = {expanded = true, scroll = 0, draws = 0, seq = -1}
_G.BALATRO_COPILOT_HUD_V1 = H
local FILE = "balatro_copilot_hud.txt"
local READY = "balatro_copilot_hud_ready.txt"
local lg = love.graphics

local function stable(value, depth)
    local kind = type(value)
    if kind == "nil" then return "n;" end
    if kind ~= "table" then
        if kind ~= "number" and kind ~= "string" and kind ~= "boolean" then return "?;" end
        local s = tostring(value)
        return kind:sub(1, 1) .. #s .. ":" .. s .. ";"
    end
    if depth <= 0 then return "t;" end
    local keys, out = {}, {"{"}
    for key in pairs(value) do
        if type(key) == "string" or type(key) == "number" then keys[#keys+1] = key end
    end
    table.sort(keys, function(a,b) return stable(a,0) < stable(b,0) end)
    for _, key in ipairs(keys) do
        out[#out+1] = stable(key,0) .. stable(value[key],depth-1)
    end
    out[#out+1] = "}"
    return table.concat(out)
end

function H.fingerprint()
    if not G or not G.GAME then return nil end
    local game = G.GAME
    local state = {identity = tostring(game), state = G.STATE, seed = game.pseudorandom and game.pseudorandom.seed,
        round = game.round, ante = game.round_resets and game.round_resets.ante,
        score = tostring(game.chips), money = game.dollars, round_data = game.current_round,
        vouchers = game.used_vouchers, modifiers = game.modifiers,
        back = game.selected_back and game.selected_back.effect, blind = {}, levels = {}, cards = {}}
    local blind = game.blind or {}
    state.blind = {name = blind.name, target = tostring(blind.chips), disabled = blind.disabled,
        boss = blind.boss, effect = blind.debuff, only_hand = blind.only_hand, hands = blind.hands}
    for name, hand in pairs(game.hands or {}) do
        state.levels[name] = {level = hand.level, chips = hand.chips, mult = hand.mult, played = hand.played}
    end
    for _, name in ipairs({"hand", "deck", "jokers", "consumeables", "discard", "play", "shop_jokers", "shop_booster", "shop_vouchers"}) do
        local area, cards = G[name], {}
        for _, card in ipairs(area and area.cards or {}) do
            cards[#cards+1] = stable({base = card.base, center = card.config and card.config.center and card.config.center.key,
                ability = card.ability, edition = card.edition, seal = card.seal, debuff = card.debuff,
                cost = card.cost, sell_cost = card.sell_cost}, 6)
        end
        -- Sorting ignores purely visual rank/suit sorting, not Joker order.
        if name ~= "jokers" then table.sort(cards) end
        state.cards[name] = cards
    end
    state.shop_limits = {jokers=G.jokers and G.jokers.config and G.jokers.config.card_limit,
        consumables=G.consumeables and G.consumeables.config and G.consumeables.config.card_limit}
    local digest = love.data.hash("sha256", stable(state, 9))
    return (digest:gsub(".", function(c) return string.format("%02x", c:byte()) end))
end

local function decode(text)
    if text:find("[^%w%%%-_%.~]") then return nil end
    local leftovers = text:gsub("%%[%da-fA-F][%da-fA-F]", "")
    if leftovers:find("%",1,true) then return nil end
    local value = text:gsub("%%([%da-fA-F][%da-fA-F])", function(h) return string.char(tonumber(h,16)) end)
    if #value > 48000 or value:find("[%z\1-\8\11\12\14-\31]") then return nil end
    return value
end

local function parse(raw)
    if type(raw) ~= "string" or #raw > 64000 then return nil end
    local lines = {}
    for line in raw:gmatch("([^\n]*)\n") do lines[#lines+1] = line end
    if #lines ~= 7 or lines[1] ~= "BACP_HUD_V1" then return nil end
    local stamp, seq = tonumber(lines[2]), tonumber(lines[3])
    if not stamp or not seq or seq < 0 or seq % 1 ~= 0 or math.abs(os.time()-stamp) > 8 then return nil end
    if lines[4] ~= "0" and lines[4] ~= "1" then return nil end
    local kinds = {idle=true,busy=true,result=true,error=true,stopped=true}
    if not kinds[lines[5]] then return nil end
    if lines[5] == "result" and (#lines[6] ~= 64 or lines[6]:find("[^a-f0-9]")) then return nil end
    local text = decode(lines[7])
    if not text then return nil end
    return {stamp=stamp, seq=seq, expanded=lines[4]=="1", kind=lines[5], fingerprint=lines[6], text=text}
end
H.parse = parse -- Pure bounded parser also used by offline contract tests.

local function active()
    return not H.failed and H.message and os.time()-H.message.stamp <= 8 and H.message.kind ~= "stopped"
end

local function clamp()
    local width, height = lg.getDimensions()
    H.width = math.min(420, math.max(220,width-24))
    H.height = H.expanded and math.min(550,math.max(120,height-40)) or 42
    H.x = math.max(0, math.min(H.x or width-H.width-24,width-H.width))
    H.y = math.max(0, math.min(H.y or 70,height-H.height))
end

local function inside(x,y)
    return active() and H.x and x >= H.x and x <= H.x+H.width and y >= H.y and y <= H.y+H.height
end

local function read_message()
    local info = love.filesystem.getInfo(FILE)
    if not info or info.size > 64000 then return end
    local message = parse(love.filesystem.read(FILE))
    if not message then return end
    if message.seq ~= H.seq or not H.message or message.text ~= H.message.text then
        H.expanded, H.scroll, H.stale = message.expanded, 0, false
        H.seq = message.seq
    end
    H.message = message
    if message.kind == "result" and message.fingerprint ~= H.fingerprint() then H.stale = true end
end

local previous_update = love.update
local elapsed = 0
function love.update(dt)
    if previous_update then previous_update(dt) end
    elapsed = elapsed + dt
    if elapsed >= 0.25 then
        elapsed = 0
        local ok, err = pcall(read_message)
        if not ok then H.failed = true; print("[Copilot HUD] Read/display error; HUD disabled: " .. tostring(err):sub(1,240)) end
    end
end

-- Tiny whitelisted presentation format. Text is always printed, never executed.
local styles = {
    M={size=13,gap=16,color={0.60,0.68,0.78,1}},
    A={size=21,gap=10,color={0.90,1,0.96,1},heading=true},
    B={size=17,gap=10,color={0.75,0.82,0.91,1},heading=true},
    P={size=16,gap=12,color={0.45,0.88,0.77,1}},
    K={size=14,gap=10,color={0.67,0.77,0.88,1}},
    R={size=15,gap=22,color={0.84,0.88,0.94,1}},
    S={size=15,gap=16,color={0.76,0.83,0.92,1}},
    F={size=12,gap=8,color={0.53,0.62,0.74,1}},
    W={size=16,gap=16,color={1,0.73,0.38,1}}}
local function font(size)
    H.fonts = H.fonts or {}
    H.fonts[size] = H.fonts[size] or lg.newFont("resources/fonts/NotoSansSC-Bold.ttf",size)
    return H.fonts[size]
end
local function layout()
    local signature = H.message.text .. tostring(H.stale) .. H.width
    if H.layout_signature == signature then return end
    local blocks = {}
    if H.stale then blocks[#blocks+1] = {tag="W",text="建议已过期 · 请按 F9 重新分析"} end
    local body = H.message.text
    if body:sub(1,15) == "BACP_BLOCKS_V1\n" then
        for line in body:sub(16):gmatch("[^\n]+") do
            local tag,text = line:match("^([MABPKRSF])\t([^\t\r\n]*)$")
            if tag and text then blocks[#blocks+1] = {tag=tag,text=text} end
            if #blocks >= 30 then break end
        end
    else
        for line in (body.."\n"):gmatch("([^\n]*)\n") do
            blocks[#blocks+1] = {tag="R",text=line}
            if #blocks >= 30 then break end
        end
    end
    local offset = 0
    for _,block in ipairs(blocks) do
        block.style = styles[block.tag]
        block.font = font(block.style.size)
        local _,lines = block.font:getWrap(block.text,H.width-36)
        block.lines,block.y = lines,offset
        block.lineheight = block.font:getHeight()*1.4
        block.height = #lines*block.lineheight
        offset = offset+block.height+block.style.gap+(block.style.heading and 8 or 0)
    end
    H.blocks,H.content_height,H.layout_signature = blocks,offset,signature
end
H.layout = layout -- Offline tests inspect actual typography and block spacing.

local function draw_panel()
    clamp()
    H.title_font = font(18)
    lg.setCanvas()
    lg.origin()
    lg.setShader()
    lg.setScissor()
    lg.setStencilTest()
    lg.setBlendMode("alpha")
    lg.setColor(0.06,0.09,0.14,0.96)
    lg.rectangle("fill",H.x,H.y,H.width,H.height,10,10)
    lg.setColor(0.33,0.77,0.69,1)
    lg.setLineWidth(1)
    lg.rectangle("line",H.x,H.y,H.width,H.height,10,10)
    lg.setFont(H.title_font)
    lg.print("AI 参谋",H.x+14,H.y+9)
    lg.print(H.expanded and "−" or "+",H.x+H.width-30,H.y+8)
    if H.expanded then
        layout()
        local available = H.height-88
        H.max_scroll = math.max(0,H.content_height-available)
        H.scroll = math.max(0,math.min(H.scroll,H.max_scroll))
        lg.setScissor(H.x+10,H.y+46,H.width-20,available)
        for _,block in ipairs(H.blocks) do
            local y = H.y+46+block.y-H.scroll
            if y+block.height >= H.y+46 and y <= H.y+46+available then
                if block.style.heading then
                    lg.setColor(block.tag=="A" and 0.09 or 0.10,0.17,0.21,1)
                    lg.rectangle("fill",H.x+10,y-4,H.width-20,block.height+8,6,6)
                    lg.setColor(0.33,0.77,0.69,1)
                    lg.rectangle("fill",H.x+10,y-4,3,block.height+8,2,2)
                end
                lg.setFont(block.font)
                local color = block.style.color
                lg.setColor(color[1],color[2],color[3],H.stale and 0.65 or 1)
                for i,line in ipairs(block.lines) do
                    lg.print(line,H.x+18,y+(i-1)*block.lineheight)
                end
            end
        end
        lg.setScissor()
        lg.setFont(font(12))
        lg.setColor(0.60,0.68,0.78,1)
        local auto=rawget(_G,"BALATRO_COPILOT_AUTO_V1")
        local lab=love.filesystem.getIdentity and love.filesystem.getIdentity()=='BalatroCopilotLab'
        lg.print(auto and auto.active and (lab and "自动闯关中 · Escape / 停止按钮" or "自动运行中 · F12 / Escape 停止") or "F9 分析 · F10 收起 · 拖标题 · 滚轮",H.x+12,H.y+H.height-29)
    end
    local mx,my = love.mouse.getPosition()
    if inside(mx,my) then
        lg.setColor(0.85,1,0.94,1)
        lg.circle("fill",mx,my,3)
    end
end

local previous_draw = love.draw
local last_ack = -10
function love.draw()
    if previous_draw then previous_draw() end
    if active() then
        lg.push("all")
        local ok, err = pcall(draw_panel)
        lg.pop() -- Restore game canvas/shader/transform/font/color even on error.
        if not ok then H.failed = true; print("[Copilot HUD] Rendering error; HUD disabled: " .. tostring(err):sub(1,240)) end
    end
    H.draws = H.draws+1
    local now = love.timer.getTime()
    if now-last_ack >= 1 then
        last_ack = now
        love.filesystem.write(READY,table.concat({"BACP_READY_V1",os.time(),H.draws,H.seq,
            H.expanded and 1 or 0,H.failed and "error" or "ok","BACP_BLOCKS_V1"},"|"))
    end
end

local previous_pressed, previous_released = love.mousepressed, love.mousereleased
local previous_moved, previous_wheel = love.mousemoved, love.wheelmoved
local captured = {}
function love.mousepressed(x,y,button,...)
    if inside(x,y) then
        captured[button] = true
        if button == 1 and y <= H.y+42 then
            if x >= H.x+H.width-42 then H.expanded = not H.expanded; H.scroll = 0; clamp()
            else H.drag = {x=x-H.x,y=y-H.y} end
        end
        return -- Never pass panel clicks through to cards/buttons below it.
    end
    if previous_pressed then return previous_pressed(x,y,button,...) end
end
function love.mousereleased(x,y,button,...)
    if captured[button] then captured[button] = nil; if button == 1 then H.drag = nil end; return end
    if previous_released then return previous_released(x,y,button,...) end
end
function love.mousemoved(x,y,...)
    if H.drag then H.x,H.y = x-H.drag.x,y-H.drag.y; clamp(); return end
    if previous_moved then return previous_moved(x,y,...) end
end
function love.wheelmoved(x,y)
    local mx,my = love.mouse.getPosition()
    if inside(mx,my) and H.expanded then H.scroll = math.max(0,math.min(H.scroll-y*55,H.max_scroll or 0)); return end
    if previous_wheel then return previous_wheel(x,y) end
end
print("[Copilot HUD] Display-only game panel loaded. Start Copilot; F9 analyzes, F10 toggles.")
