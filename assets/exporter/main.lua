-- Balatro State Exporter 0.1.0
-- Read-only by design: this file reads G and only writes its own export files.

local MOD = SMODS.current_mod
local EXPORT_JSON = "balatro_state.json"
local EXPORT_TXT = "balatro_state.txt"

local function json_escape(value)
    return tostring(value)
        :gsub("\\", "\\\\")
        :gsub('"', '\\"')
        :gsub("\b", "\\b")
        :gsub("\f", "\\f")
        :gsub("\n", "\\n")
        :gsub("\r", "\\r")
        :gsub("\t", "\\t")
        :gsub("[%z\1-\31]", function(c)
            return string.format("\\u%04x", string.byte(c))
        end)
end

local function is_array(value)
    local count = 0
    local max_index = 0
    for key, _ in pairs(value) do
        if type(key) ~= "number" or key < 1 or key % 1 ~= 0 then return false end
        count = count + 1
        if key > max_index then max_index = key end
    end
    return count == max_index
end

local function json_encode(value, indent)
    indent = indent or 0
    local kind = type(value)
    if value == nil then return "null" end
    if kind == "boolean" then return value and "true" or "false" end
    if kind == "number" then
        if value ~= value or value == math.huge or value == -math.huge then
            return '"' .. json_escape(value) .. '"'
        end
        return tostring(value)
    end
    if kind == "string" then return '"' .. json_escape(value) .. '"' end
    if kind ~= "table" then return '"' .. json_escape(value) .. '"' end

    local pad = string.rep("  ", indent)
    local next_pad = string.rep("  ", indent + 1)
    local parts = {}
    if is_array(value) then
        for i = 1, #value do
            parts[#parts + 1] = next_pad .. json_encode(value[i], indent + 1)
        end
        if #parts == 0 then return "[]" end
        return "[\n" .. table.concat(parts, ",\n") .. "\n" .. pad .. "]"
    end

    local keys = {}
    for key, _ in pairs(value) do keys[#keys + 1] = tostring(key) end
    table.sort(keys)
    for _, key in ipairs(keys) do
        parts[#parts + 1] = next_pad .. '"' .. json_escape(key) .. '": ' .. json_encode(value[key], indent + 1)
    end
    if #parts == 0 then return "{}" end
    return "{\n" .. table.concat(parts, ",\n") .. "\n" .. pad .. "}"
end

local function safe_string(value)
    if value == nil then return nil end
    local ok, result = pcall(tostring, value)
    return ok and result or "<unprintable>"
end

local rank_short = {
    [14] = "A", [13] = "K", [12] = "Q", [11] = "J", [10] = "T",
    [9] = "9", [8] = "8", [7] = "7", [6] = "6", [5] = "5",
    [4] = "4", [3] = "3", [2] = "2"
}

local suit_short = {
    Spades = "S", Hearts = "H", Clubs = "C", Diamonds = "D"
}

local function edition_name(edition)
    if not edition then return nil end
    if edition.negative then return "negative" end
    if edition.polychrome then return "polychrome" end
    if edition.holo then return "holographic" end
    if edition.foil then return "foil" end
    return edition.key or edition.type
end

local function playing_card(card)
    local base = card and card.base or {}
    local ability = card and card.ability or {}
    local center = card and card.config and card.config.center or {}
    local rank = rank_short[base.id] or safe_string(base.value or base.id) or "?"
    local suit = suit_short[base.suit] or safe_string(base.suit) or "?"
    return {
        code = rank .. suit,
        rank = safe_string(base.value or base.id),
        rank_id = base.id,
        suit = safe_string(base.suit),
        enhancement = center.key ~= "c_base" and safe_string(center.key or ability.name) or nil,
        seal = safe_string(card and card.seal),
        edition = edition_name(card and card.edition),
        debuffed = not not (card and card.debuff),
        highlighted = not not (card and card.highlighted)
    }
end

local function joker_card(card)
    local center = card and card.config and card.config.center or {}
    local ability = card and card.ability or {}
    return {
        key = safe_string(center.key),
        name = safe_string(ability.name or center.name),
        edition = edition_name(card and card.edition),
        eternal = not not ability.eternal,
        perishable = not not ability.perishable,
        rental = not not ability.rental,
        debuffed = not not (card and card.debuff),
        sell_cost = card and card.sell_cost or nil
    }
end

local function map_cards(area, mapper)
    local result = {}
    if area and area.cards then
        for i, card in ipairs(area.cards) do result[i] = mapper(card) end
    end
    return result
end

local function state_name()
    if not G then return "NO_GAME" end
    for key, value in pairs(G.STATES or {}) do
        if value == G.STATE then return key end
    end
    return safe_string(G.STATE) or "UNKNOWN"
end

local function collect_state()
    if not G or not G.GAME then return nil, "No active run is loaded." end
    local round = G.GAME.current_round or {}
    local blind = G.GAME.blind or {}
    local blind_config = blind.config and blind.config.blind or {}
    local state = {
        schema_version = 1,
        exported_at = os.date("!%Y-%m-%dT%H:%M:%SZ"),
        balatro_version = safe_string(G.VERSION),
        game_state = state_name(),
        ante = G.GAME.round_resets and G.GAME.round_resets.ante or nil,
        round = G.GAME.round or nil,
        money = G.GAME.dollars,
        score = safe_string(G.GAME.chips or 0),
        hands_left = round.hands_left,
        discards_left = round.discards_left,
        hands_played = round.hands_played,
        discards_used = round.discards_used,
        blind = {
            key = safe_string(blind_config.key),
            name = safe_string(blind.name or blind_config.name),
            target = safe_string(blind.chips),
            disabled = not not blind.disabled,
            boss = not not blind.boss
        },
        hand = map_cards(G.hand, playing_card),
        deck_remaining = map_cards(G.deck, playing_card),
        played_area = map_cards(G.play, playing_card),
        jokers = map_cards(G.jokers, joker_card),
        counts = {
            hand = G.hand and G.hand.cards and #G.hand.cards or 0,
            deck_remaining = G.deck and G.deck.cards and #G.deck.cards or 0,
            jokers = G.jokers and G.jokers.cards and #G.jokers.cards or 0
        }
    }
    return state
end

local function join_card_codes(cards)
    local result = {}
    for _, card in ipairs(cards or {}) do
        local suffix = ""
        if card.enhancement then suffix = suffix .. ":" .. card.enhancement end
        if card.seal then suffix = suffix .. ":" .. card.seal end
        if card.edition then suffix = suffix .. ":" .. card.edition end
        if card.debuffed then suffix = suffix .. ":debuffed" end
        result[#result + 1] = card.code .. suffix
    end
    return table.concat(result, " ")
end

local function compact_text(state)
    local joker_names = {}
    for _, joker in ipairs(state.jokers or {}) do
        local text = joker.name or joker.key or "Unknown Joker"
        if joker.edition then text = text .. "[" .. joker.edition .. "]" end
        if joker.debuffed then text = text .. "[debuffed]" end
        joker_names[#joker_names + 1] = text
    end
    return table.concat({
        "Balatro " .. (state.balatro_version or "unknown"),
        "State=" .. (state.game_state or "unknown"),
        "Ante=" .. safe_string(state.ante or "?"),
        "Blind=" .. (state.blind.name or state.blind.key or "?") .. " " .. (state.score or "0") .. "/" .. (state.blind.target or "?"),
        "Hands=" .. safe_string(state.hands_left or "?"),
        "Discards=" .. safe_string(state.discards_left or "?"),
        "Money=" .. safe_string(state.money or "?"),
        "Hand=" .. join_card_codes(state.hand),
        "Jokers=" .. table.concat(joker_names, " | "),
        "DeckRemaining=" .. safe_string(state.counts.deck_remaining),
        "DeckCards=" .. join_card_codes(state.deck_remaining)
    }, "\n")
end

local function export_state()
    local state, err = collect_state()
    if not state then
        print("[State Exporter] " .. err)
        return
    end

    local text = compact_text(state)
    local json = json_encode(state) .. "\n"
    local json_ok, json_err = love.filesystem.write(EXPORT_JSON, json)
    local text_ok, text_err = love.filesystem.write(EXPORT_TXT, text .. "\n")
    local clipboard_ok, clipboard_err = pcall(love.system.setClipboardText, text)

    if json_ok and text_ok and clipboard_ok then
        print("[State Exporter] Copied state and wrote " .. EXPORT_JSON .. " + " .. EXPORT_TXT)
        if play_sound then pcall(play_sound, "coin1", 1, 0.25) end
    else
        print("[State Exporter] Export failed: " .. safe_string(json_err or text_err or clipboard_err))
    end
end

if not rawget(_G, "BALATRO_STATE_EXPORTER_INSTALLED") then
    _G.BALATRO_STATE_EXPORTER_INSTALLED = true
    local previous_keypressed = love.keypressed
    function love.keypressed(key, scancode, isrepeat)
        if key == "f8" and not isrepeat then export_state() end
        if previous_keypressed then return previous_keypressed(key, scancode, isrepeat) end
    end
    print("[State Exporter] Loaded. Press F8 to copy/export the current state.")
end

