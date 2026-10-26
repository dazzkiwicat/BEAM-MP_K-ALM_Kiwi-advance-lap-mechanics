-- kkLap : client-side extension (runs inside the game on each player's PC)
--
-- Only THREE calls here are BeamMP:  TriggerServerEvent, AddEventHandler, MPVehicleGE.getOwnMap
-- Everything else (be:, veh:getPosition, guihooks.trigger, log, jsonEncode, ffi.C draw calls)
-- is BeamNG game Lua that happens to be available because we run inside the game. Keep that straight.
--
-- Job: each frame, for every vehicle THIS client owns (split-screen = two of them),
-- check whether it just entered the next checkpoint sphere. If yes, tell the server.
-- The client never times anything. It reports crossings. The server keeps the clock.
-- Since v0.4.0 it also DRAWS the checkpoints in the world so you can see what you set.
--
-- Debug: every log line is tagged [KKLAP]. Grep the game log:
--     findstr KKLAP beamng.log      (in the BeamNG user folder; never paste the whole log)
-- and, when cfg.debug is on, the same lines are sent to the server log too.

local M = {}
M.VERSION = '0.4.0'   -- bump this; the zip name and the log lines follow it

local cfg      = { debug = true, markers = true, checkpoints = {} }
local state    = {}          -- [vid] = { nextCp = n, inside = bool }
local lastDbg  = 0

local function dbg(msg)
    log('I', 'kkLap', '[KKLAP] ' .. msg)   -- 'I' not 'D': BeamMP itself only logs E/W/I
    if cfg.debug then TriggerServerEvent('kkLap_dbg', msg) end
end

local function toast(msg, ttl)
    -- BeamNG UI message. Same call BeamMP's own MPVehicleGE.lua uses.
    guihooks.trigger('Message', { ttl = ttl or 5, msg = msg, icon = 'timer' })
end

-- ----------------------------------------------------- world drawing (BeamNG FFI)
-- The same two draw calls BeamMP's own MPVehicleGE.lua uses for name tags and blobs:
--   ffi.C.BNG_DBG_DRAW_Sphere(x, y, z, radius, colour, useZ)
--   ffi.C.BNG_DBG_DRAW_TextAdvanced(x, y, z, text, fg, drawBg, false, bg, shadow, useZ)
-- colour() is BeamNG's packed colour, 0-255 per channel. Everything is fetched inside one
-- pcall: if the game build moved it (or we are on the bench, where there is no ffi) the
-- markers silently become no-ops and the TIMER STILL WORKS. Markers are a luxury.
local draw = { ok = false }
do
    local ok, r = pcall(function()
        return {
            sphere = ffi.C.BNG_DBG_DRAW_Sphere,
            text   = ffi.C.BNG_DBG_DRAW_TextAdvanced,
            start  = color(0, 220, 0, 90),       -- start/finish: green
            cp     = color(255, 150, 0, 70),     -- other checkpoints: orange
            next   = color(0, 160, 255, 120),    -- the one YOUR car must hit next: blue, brighter
            fg     = color(255, 255, 255, 254),  -- 254 not 255: BeamMP notes 255 breaks the bg alpha
            bg     = color(0, 0, 0, 160),
        }
    end)
    if ok and r then draw = r; draw.ok = true end
end

-- ----------------------------------------------------- events from the server
local function onCfg(data)
    local d = jsonDecode(data)
    if not d then dbg('bad cfg json'); return end
    cfg = d
    if cfg.markers == nil then cfg.markers = true end   -- config.json from before v0.4.0
    state = {}                                    -- checkpoints changed, start fresh
    dbg(string.format('cfg received: %d checkpoints, debug=%s, markers=%s',
        #cfg.checkpoints, tostring(cfg.debug), tostring(cfg.markers)))
end

local function onLap(data)
    local d = jsonDecode(data)
    if not d then return end
    toast(string.format('%s  lap %d  %s  (best %s)', d.name, d.laps, M.fmt(d.lapMs), M.fmt(d.bestMs)), 6)
end

local function onSplit(data)
    local d = jsonDecode(data)
    if not d then return end
    toast(string.format('split %d  %s', d.cp, M.fmt(d.splitMs)), 3)
end

local function onInfo(data)
    local d = jsonDecode(data)
    if d and d.msg then dbg('server: ' .. d.msg) end
end

-- server asked "where is your car" for /lap mark. Use the player's current vehicle.
local function onGetPos(data)
    local d   = jsonDecode(data) or {}
    local veh = be:getPlayerVehicle(0)             -- BeamNG: seat 0's vehicle
    if not veh then dbg('getpos: no player vehicle'); return end
    local p = veh:getPosition()
    TriggerServerEvent('kkLap_posreply', jsonEncode({ r = d.r, pos = { x = p.x, y = p.y, z = p.z } }))
    dbg(string.format('pos sent %.1f %.1f %.1f', p.x, p.y, p.z))
end

-- ----------------------------------------------------- per-frame check
local function onUpdate(dt)
    -- every 5 s, when debug is on, prove the loop is alive and show what it sees.
    -- ABOVE the "no checkpoints" exit on purpose: v0.3.0 had it below, so on an empty
    -- track it never fired and the live test could not prove onUpdate was wired (26/10).
    if cfg.debug then
        lastDbg = lastDbg + dt
        if lastDbg > 5 then
            lastDbg = 0
            local ids = {}
            for vid in pairs(MPVehicleGE.getOwnMap()) do table.insert(ids, tostring(vid)) end
            dbg('alive, own vehicles: ' .. (#ids > 0 and table.concat(ids, ',') or 'none'))
        end
    end

    local n = #cfg.checkpoints
    if n == 0 then return end

    -- BeamMP: table of the vehicles THIS client owns, keyed by game vehicle id.
    -- Split-screen = two entries. Remote players' cars are not in it.
    for vid, _ in pairs(MPVehicleGE.getOwnMap()) do
        local veh = be:getObjectByID(vid)             -- BeamNG: id -> object
        if veh then
            local s  = state[vid] or { nextCp = 1, inside = false }
            state[vid] = s
            local cp = cfg.checkpoints[s.nextCp]
            local p  = veh:getPosition()
            local dx, dy, dz = p.x - cp.x, p.y - cp.y, p.z - cp.z
            local inside = (dx*dx + dy*dy + dz*dz) <= (cp.r * cp.r)

            if inside and not s.inside then
                -- edge: just entered the sphere. Report once.
                TriggerServerEvent('kkLap_cross', jsonEncode({ vid = vid, cp = s.nextCp, pos = { x = p.x, y = p.y, z = p.z } }))
                dbg(string.format('vid %d entered cp %d', vid, s.nextCp))
                s.nextCp = (s.nextCp % n) + 1
                inside   = false   -- new target sphere: start outside it, or touching spheres get missed (bench, 26/10)
            end
            s.inside = inside
        end
    end
end

-- ----------------------------------------------------- per-frame drawing
-- BeamMP draws its name tags from onPreRender, not onUpdate, so that is where ours go too.
-- Each checkpoint is drawn as the exact sphere the timer uses, so what you see is what
-- triggers. Label floats above it. Your next checkpoint is blue, start/finish green, rest orange.
local function onPreRender(dt)
    if not draw.ok or not cfg.markers then return end
    local n = #cfg.checkpoints
    if n == 0 then return end

    local nextCp                                  -- for the car in seat 0, so it can be highlighted
    local pv = be:getPlayerVehicle(0)
    if pv then
        local s = state[pv:getID()]
        nextCp = s and s.nextCp
    end

    for i, cp in ipairs(cfg.checkpoints) do
        local c = (i == nextCp) and draw.next or ((i == 1) and draw.start or draw.cp)
        draw.sphere(cp.x, cp.y, cp.z, cp.r, c, true)
        local label = (i == 1) and 'START / FINISH' or ('CP ' .. i)
        if i == nextCp then label = '> ' .. label .. ' <' end
        draw.text(cp.x, cp.y, cp.z + cp.r + 1.0, label, draw.fg, true, false, draw.bg, false, true)
    end
end

function M.fmt(ms)
    if not ms then return '-' end
    return string.format('%d:%06.3f', math.floor(ms / 60000), (ms % 60000) / 1000)
end

local function onExtensionLoaded()
    AddEventHandler('kkLap_cfg',    onCfg)
    AddEventHandler('kkLap_lap',    onLap)
    AddEventHandler('kkLap_split',  onSplit)
    AddEventHandler('kkLap_info',   onInfo)
    AddEventHandler('kkLap_getpos', onGetPos)
    dbg('extension v' .. M.VERSION .. ' loaded, markers ' .. (draw.ok and 'available' or 'NOT available') .. ', asking server for config')
    TriggerServerEvent('kkLap_getcfg', '')   -- usually too early to arrive; onPlayerJoin on the server covers it (26/10)
end

M.onExtensionLoaded = onExtensionLoaded
M.onUpdate          = onUpdate
M.onPreRender       = onPreRender
return M
