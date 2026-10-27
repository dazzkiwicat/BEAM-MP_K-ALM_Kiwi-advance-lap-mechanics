-- kkLap vehicle-side extension: runs INSIDE each car's own Lua VM (BeamNG "vehicle Lua").
-- Loaded into every spawned car by the GE side with extensions.loadModulesInDirectory,
-- the same way BeamMP loads its own vehicle extensions (MPVehicleGE.lua onVehicleSpawned).
-- GE calls us as kkLapVE.<fn>()  (bare file name, like BeamMP's "positionVE.").
--
-- Two jobs: (1) when the horn goes on, tell the GE side which car honked;
--           (2) when asked, report what surface the wheels are on (tarseal or not).
-- Nothing here is BeamMP. electrics, wheels, particles, obj:* are BeamNG vehicle Lua.
local M = {}
local was  = false
local mats = nil

local function updateGFX(dt)
    local h  = electrics.values.horn
    local on = (h == true) or (type(h) == 'number' and h > 0)
    if on and not was then
        obj:queueGameEngineLua("kkLap.onHorn(" .. obj:getID() .. ")")   -- GE global 'kkLap' = our extension
    end
    was = on
end

-- Ground model under each wheel. Stock pattern (vehicle sounds.lua / input.lua, 0.40 dump):
-- contactMaterialID1/2 are the two touching materials, 4 = RUBBER is the tyre, so take the
-- other one; -1 = not touching anything. particles turns the id into a name (ASPHALT, GRASS...).
local SEALED = { ASPHALT = true, ASPHALT_WET = true, COBBLESTONE = true, RUMBLE_STRIP = true }
local function wheelGround(wd)
    local a, b = wd.contactMaterialID1, wd.contactMaterialID2
    if a == 4 then a, b = b, a end
    if not a or a < 0 then return 'NONE' end
    local m = particles.getMaterialByID(mats, a)
    return (m and m.name) or tostring(a)
end

-- GE asks with veh:queueLuaCommand('kkLapVE.reportSurface()'); we answer kkLap.onSurface(id, sealed, "FL=ASPHALT,...")
local function reportSurface()
    mats = mats or particles.getMaterialsParticlesTable()
    local parts, touching, sealed = {}, 0, 0
    for _, wd in pairs(wheels.wheels) do
        local name = wheelGround(wd)
        table.insert(parts, tostring(wd.name or '?') .. '=' .. name)
        if name ~= 'NONE' then
            touching = touching + 1
            if SEALED[name] then sealed = sealed + 1 end
        end
    end
    local ok = (touching > 0) and (sealed == touching)
    obj:queueGameEngineLua(string.format('kkLap.onSurface(%d, %s, %q)', obj:getID(), tostring(ok), table.concat(parts, ',')))
end

-- BeamNG calls onReset in vehicle Lua on every reset (Insert key, recover, teleport...). BeamMP's own
-- VE files hook it the same way. GE decides whether the car moved (position reset) or not (physics reset).
local function onReset()
    was = false
    obj:queueGameEngineLua("kkLap.onVehReset(" .. obj:getID() .. ")")
end

M.updateGFX     = updateGFX
M.onReset       = onReset
M.reportSurface = reportSurface
return M
