        -- BALATRO_COPILOT_SCORING_SNAPSHOT_V2
        -- Copy metadata only. Never call eval_card/calculate_joker/evaluate_play.
        local back = G.GAME.selected_back
        local other_mods = {}
        for id, mod in pairs((SMODS and SMODS.Mods) or {}) do
            if id ~= "Steamodded" and id ~= "steamodded" and id ~= "Lovely" and id ~= "Balatro"
                and id ~= "balatro_state_exporter" and id ~= "balatro_copilot_hud" and id ~= "balatro_copilot_autoplay"
                and not mod.disabled and mod.can_load ~= false then other_mods[#other_mods+1] = id end
        end
        table.sort(other_mods)
        state.scoring_rules = {version = 2,
            back_key = back and back.effect and back.effect.center and back.effect.center.key,
            modifiers = scalar_copy(G.GAME.modifiers, 3), challenge = not not G.GAME.challenge,
            other_mods = other_mods}
