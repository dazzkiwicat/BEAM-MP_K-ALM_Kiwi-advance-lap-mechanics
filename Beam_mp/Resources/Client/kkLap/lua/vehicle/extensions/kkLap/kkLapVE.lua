-- kkLap vehicle-side extension: runs INSIDE each car's own Lua VM (BeamNG "vehicle Lua").
-- Loaded into every spawned car by the GE side with extensions.loadModulesInDirectory,
-- the same way BeamMP loads its own vehicle extensions (MPVehicleGE.lua onVehicleSpawned).
--
-- One job: when the horn goes on, tell the GE side which car honked.
-- Nothing here is BeamMP. electrics.values.horn and obj:queueGameEngineLua are BeamNG vehicle Lua.
local M = {}
local was = false

local function updateGFX(dt)
    local h  = electrics.values.horn
    local on = (h == true) or (type(h) == 'number' and h > 0)
    if on and not was then
        obj:queueGameEngineLua("kkLap.onHorn(" .. obj:getID() .. ")")   -- GE global 'kkLap' = our extension
    end
    was = on
end

local function onReset() was = false end

M.updateGFX = updateGFX
M.onReset   = onReset
return M
