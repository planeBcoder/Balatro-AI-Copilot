        -- BALATRO_COPILOT_AUTO_SNAPSHOT_V1
        local auto = rawget(_G, "BALATRO_COPILOT_AUTO_V1")
        if auto then
            local ok, snapshot = pcall(auto.snapshot)
            if ok then state.autoplay = snapshot end
        end
