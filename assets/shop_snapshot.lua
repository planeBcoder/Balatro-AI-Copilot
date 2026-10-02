        -- BALATRO_COPILOT_SHOP_SNAPSHOT_V1
        -- Passive snapshot only: no callbacks, RNG, saves, or execution commands.
        if state.game_state == 'SHOP' then
            local function offers(area)
                local out = {}
                for i,c in ipairs(area and area.cards or {}) do
                    local center = c.config and c.config.center or {}
                    local a = c.ability or {}
                    local loc = G.localization and G.localization.descriptions
                    local desc = loc and loc[a.set] and loc[a.set][center.key]
                    out[i] = {key=center.key, name=a.name or center.name, set=a.set,
                        cost=c.cost, sell_cost=c.sell_cost, ability=scalar_copy(a,6),
                        edition=edition_name(c.edition), eternal=not not a.eternal,
                        perishable=not not a.perishable, rental=not not a.rental,
                        debuffed=not not c.debuff, description=scalar_copy(desc and desc.text,3)}
                    if localize and center.key and a.set then
                        local ok,name=pcall(localize,{type='name_text',set=a.set,key=center.key})
                        if ok and type(name)=='string' and name~='ERROR' then out[i].display_name=name end
                    end
                end
                return out
            end
            local pool = {}
            for i,c in ipairs(G.playing_cards or {}) do
                pool[i]=playing_card(c)
                pool[i].base_bonus=c.ability and c.ability.bonus or 0
                pool[i].permanent_bonus=c.ability and c.ability.perma_bonus or 0
            end
            local g=G.GAME
            local next_blind
            for _,name in ipairs({'Small','Big','Boss'}) do
                if g.round_resets and g.round_resets.blind_states and g.round_resets.blind_states[name]=='Upcoming' then
                    next_blind=name;break
                end
            end
            state.shop_snapshot={version=1, ready=G.shop~=nil and not G.OVERLAY_MENU
                and not (G.CONTROLLER and G.CONTROLLER.locked)
                and (g.STOP_USE or 0)<=0 and not G.SETTINGS.paused,
                offers=offers(G.shop_jokers), boosters=offers(G.shop_booster),
                vouchers=offers(G.shop_vouchers), jokers=offers(G.jokers),
                consumables=offers(G.consumeables), full_deck=pool,
                joker_limit=G.jokers and G.jokers.config.card_limit,
                consumable_limit=G.consumeables and G.consumeables.config.card_limit,
                hand_size=G.hand and G.hand.config.card_limit,
                hands=g.round_resets and g.round_resets.hands,
                discards=g.round_resets and g.round_resets.discards,
                bankrupt_at=g.bankrupt_at or 0, reroll_cost=g.current_round.reroll_cost,
                interest_amount=g.interest_amount or 1, interest_cap=g.interest_cap or 25,
                no_interest=g.modifiers and g.modifiers.no_interest or false,
                next_blind=next_blind,
                blind_states=scalar_copy(g.round_resets and g.round_resets.blind_states,3),
                blind_choices=scalar_copy(g.round_resets and g.round_resets.blind_choices,3),
                used_vouchers=scalar_copy(g.used_vouchers,3)}
        end
        -- BALATRO_COPILOT_SHOP_SNAPSHOT_END_V1
