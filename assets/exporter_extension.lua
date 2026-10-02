-- BALATRO_COPILOT_BRIDGE_V1
-- Appended to the existing exporter. Its F8 behaviour is left intact.
-- Local nonce request/response, no commands, no network, no game mutation.
do
    local elapsed = 0
    local previous_update = love.update
    local last_request = love.filesystem.read("balatro_copilot_request.txt")
    local function scalar_copy(value, depth)
        local kind = type(value)
        if kind == "string" or kind == "boolean" then return value end
        if kind == "number" then return value end
        if kind ~= "table" or depth <= 0 then return nil end
        local result = {}
        for k, v in pairs(value) do
            if type(k) == "string" or type(k) == "number" then
                local copied = scalar_copy(v, depth - 1)
                if copied ~= nil then result[k] = copied end
            end
        end
        return result
    end
    local function answer(request)
        local token, timestamp = request:match("^([a-f0-9]+)|(%d+)$")
        if not token or #token ~= 32 or math.abs(os.time() - tonumber(timestamp)) > 10 then return end
        local state = collect_state()
        if not state then return end
        state.request_id = token
        -- BALATRO_COPILOT_HUD_FINGERPRINT_V1
        local hud = rawget(_G, "BALATRO_COPILOT_HUD_V1")
        if hud then
            local ok, signature = pcall(hud.fingerprint)
            if ok then state.hud_fingerprint = signature end
        end
        state.read_only_bridge_version = 1
        state.poker_hands = {}
        for name, hand in pairs(G.GAME.hands or {}) do
            state.poker_hands[name] = {level = hand.level, chips = hand.chips, mult = hand.mult, played = hand.played}
        end
        for i, joker in ipairs((G.jokers and G.jokers.cards) or {}) do
            state.jokers[i].ability = scalar_copy(joker.ability, 3)
        end
        for _, pair in ipairs({{G.hand, state.hand}, {G.deck, state.deck_remaining}}) do
            for i, card in ipairs((pair[1] and pair[1].cards) or {}) do
                pair[2][i].permanent_bonus = card.ability and card.ability.perma_bonus or 0
                pair[2][i].base_bonus = card.ability and card.ability.bonus or 0
            end
        end
        state.consumables = map_cards(G.consumeables, joker_card)
        state.vouchers = scalar_copy(G.GAME.used_vouchers, 2)
        state.discarded_area = map_cards(G.discard, playing_card)
        state.cards_played = scalar_copy(G.GAME.cards_played, 3)
        state.blind.effect = scalar_copy(G.GAME.blind and G.GAME.blind.debuff, 2)
        state.blind.only_hand = G.GAME.blind and G.GAME.blind.only_hand or nil
        state.blind.played_hand_types = scalar_copy(G.GAME.blind and G.GAME.blind.hands, 1)
        state.hand_limit = G.hand and G.hand.config and G.hand.config.card_limit
        -- Non-atomic game write is safe: reader requires valid JSON + this nonce.
        love.filesystem.write("balatro_copilot_response.json", json_encode(state) .. "\n")
    end
    function love.update(dt)
        if previous_update then previous_update(dt) end
        elapsed = elapsed + dt
        if elapsed >= 0.08 then
            elapsed = 0
            local request = love.filesystem.read("balatro_copilot_request.txt")
            if request and request ~= last_request then
                last_request = request
                local ok = pcall(answer, request)
                if not ok then print("[State Exporter] Copilot export unavailable") end
            end
        end
    end
    love.filesystem.write("balatro_copilot_ready.txt", "bridge-v1")
end
