-- kkLap : client-side extension (runs inside the game on each player's PC)
--
-- Only THREE calls here are BeamMP:  TriggerServerEvent, AddEventHandler, MPVehicleGE.getOwnMap
-- Everything else (be:, veh:getPosition, guihooks.trigger, log, jsonEncode) is BeamNG game Lua
-- that happens to be available because we run inside the game. Keep that straight.
--
-- Job: each frame, for every vehicle THIS client owns (split-screen = two of them),
-- check whether it just entered the next checkpoint sphere. If yes, tell the server.
-- The client never times anything. It reports crossings. The server keeps the clock.
--
-- Debug: every log line is tagged [KKLAP]. Grep the game log:
--     grep KKLAP beamng.log        (in the BeamNG user folder)
-- and, when cfg.debug is on, the same lines are sent to the server log too.

local M = {}

local cfg      = { debug = true, checkpoints = {} }
local state    = {}          -- [vid] = { nextCp = n, inside = bool }
local lastDbg  = 0

local function dbg(msg)
    log('I', 'kkLap', '[KKLAP] ' .. msg)   -- 'I' not 'D': BeamMP itself only logs E/W/I
    if cfg.debug then TriggerServerEvent('kkLap:dbg', msg) end
end

local function toast(msg, ttl)
    -- BeamNG UI message. Same call BeamMP's own MPVehicleGE.lua uses.
    guihooks.trigger('Message', { ttl = ttl or 5, msg = msg, icon = 'timer' })
end

-- ----------------------------------------------------- events from the server
local function onCfg(data)
    local d = jsonDecode(data)
    if not d then dbg('bad cfg json'); return end
    cfg = d
    state = {}                                    -- checkpoints changed, start fresh
    dbg('cfg received: ' .. #cfg.checkpoints .. ' checkpoints, debug=' .. tostring(cfg.debug))
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
    TriggerServerEvent('kkLap:posreply', jsonEncode({ r = d.r, pos = { x = p.x, y = p.y, z = p.z } }))
    dbg(string.format('pos sent %.1f %.1f %.1f', p.x, p.y, p.z))
end

-- ----------------------------------------------------- per-frame check
local function onUpdate(dt)
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
                TriggerServerEvent('kkLap:cross', jsonEncode({ vid = vid, cp = s.nextCp, pos = { x = p.x, y = p.y, z = p.z } }))
                dbg(string.format('vid %d entered cp %d', vid, s.nextCp))
                s.nextCp = (s.nextCp % n) + 1
                inside   = false   -- new target sphere: start outside it, or touching spheres get missed (bench, 26/10)
            end
            s.inside = inside
        end
    end

    -- every 5 s, when debug is on, prove the loop is alive and show what it sees
    if cfg.debug then
        lastDbg = lastDbg + dt
        if lastDbg > 5 then
            lastDbg = 0
            local ids = {}
            for vid in pairs(MPVehicleGE.getOwnMap()) do table.insert(ids, tostring(vid)) end
            dbg('alive, own vehicles: ' .. (#ids > 0 and table.concat(ids, ',') or 'none'))
        end
    end
end

function M.fmt(ms)
    if not ms then return '-' end
    return string.format('%d:%06.3f', math.floor(ms / 60000), (ms % 60000) / 1000)
end

local function onExtensionLoaded()
    AddEventHandler('kkLap:cfg',    onCfg)
    AddEventHandler('kkLap:lap',    onLap)
    AddEventHandler('kkLap:split',  onSplit)
    AddEventHandler('kkLap:info',   onInfo)
    AddEventHandler('kkLap:getpos', onGetPos)
    dbg('extension loaded, asking server for config')
    TriggerServerEvent('kkLap:getcfg', '')
end

M.onExtensionLoaded = onExtensionLoaded
M.onUpdate          = onUpdate
return M
