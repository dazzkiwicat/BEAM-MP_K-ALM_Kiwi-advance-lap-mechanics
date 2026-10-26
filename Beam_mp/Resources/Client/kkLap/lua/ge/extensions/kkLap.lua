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
M.VERSION = '0.7.0'   -- bump this; the zip name and the log lines follow it

local cfg      = { debug = true, markers = true, checkpoints = {} }
local state    = {}          -- [vid] = { nextCp, inside, started, flashCp, flashT, armed, jumpSent }
local counting = false       -- a shared countdown is running (server says so)
local lastDbg  = 0

local function dbg(msg)
    log('I', 'kkLap', '[KKLAP] ' .. msg)   -- 'I' not 'D': BeamMP itself only logs E/W/I
    if cfg.debug then TriggerServerEvent('kkLap_dbg', msg) end
end

local function toast(msg, ttl)
    -- BeamNG UI message, small, top left. Same call BeamMP's own MPVehicleGE.lua uses.
    guihooks.trigger('Message', { ttl = ttl or 5, msg = msg, icon = 'timer' })
end

-- Big centred flash, the one the game's own scenarios use for 3-2-1-GO and lap times.
-- Needs the "Race Countdown" UI app on the player's layout (docs.beammp.com/beamng/lua-snippets).
-- Without it nothing shows and nothing breaks, which is why every flash also sends a toast.
-- sound: a Lua string the UI runs, e.g. Engine.Audio.playOnce('AudioGui', 'event:UI_CountdownGo'); 0 = none.
local function flash(msg, ttl, big, sound)
    guihooks.trigger('ScenarioFlashMessage', { { msg, ttl or 2, sound or 0, big ~= false } })
end
local function beep(name) return "Engine.Audio.playOnce('AudioGui', 'event:UI_" .. name .. "')" end

-- Mid-size persistent centre text: the running lap clock. Needs the "Race Realtime Display" UI app.
local function realtime(msg) guihooks.trigger('ScenarioRealtimeDisplay', { msg = msg }) end
local lapClock  = { running = false, t = 0, hold = 0, holdMsg = nil, shown = 0 }

-- ----------------------------------------------------- world drawing (BeamNG FFI)
-- The same two draw calls BeamMP's own MPVehicleGE.lua uses for name tags and blobs:
--   ffi.C.BNG_DBG_DRAW_Sphere(x, y, z, radius, colour, useZ)
--   ffi.C.BNG_DBG_DRAW_TextAdvanced(x, y, z, text, fg, drawBg, false, bg, shadow, useZ)
-- colour() is BeamNG's packed colour, 0-255 per channel. Everything is fetched inside one
-- pcall: if the game build moved it (or we are on the bench, where there is no ffi) the
-- markers silently become no-ops and the TIMER STILL WORKS. Markers are a luxury.
local draw = { ok = false }
-- Transparency is one number, cfg.markerAlpha (0 invisible .. 255 solid), set live with
-- /lap set markerAlpha 40. Everything else is derived from it. v0.5.1 looked solid (Darren).
local function buildColours(a)
    a = math.max(0, math.min(255, math.floor(tonumber(a) or 40)))
    draw.start = color(0, 220, 0, a)                          -- start/finish: green
    draw.cp    = color(255, 150, 0, a)                        -- other checkpoints: orange
    draw.next  = color(0, 160, 255, math.min(255, a + 20))    -- YOUR next one: blue, a touch stronger
    draw.done  = color(120, 120, 120, math.floor(a / 2))      -- been through it: grey ghost
    draw.flash = color(255, 255, 255, math.min(255, a * 2))   -- the 1.5 s after crossing: white
    draw.fg    = color(255, 255, 255, 254)                    -- 254 not 255: BeamMP notes 255 breaks the bg alpha
    draw.bg    = color(0, 0, 0, 160)
end
do
    local ok = pcall(function()
        draw.sphere = ffi.C.BNG_DBG_DRAW_Sphere
        draw.text   = ffi.C.BNG_DBG_DRAW_TextAdvanced
        buildColours(cfg.markerAlpha)
    end)
    draw.ok = ok and draw.sphere ~= nil
end

-- ----------------------------------------------------- events from the server
local function onCfg(data)
    local d = jsonDecode(data)
    if not d then dbg('bad cfg json'); return end
    if d.yourPid ~= nil then M.myPid = d.yourPid end   -- server stamps the receiver's pid on a direct send
    cfg = d
    if cfg.markers == nil then cfg.markers = true end   -- config.json from before v0.4.0
    lapClock.running = false; realtime('')              -- track changed or reset: clock off
    if draw.ok then buildColours(cfg.markerAlpha) end   -- /lap set markerAlpha takes effect here
    state = {}                                    -- checkpoints changed, start fresh
    dbg(string.format('cfg received: %d checkpoints, debug=%s, markers=%s',
        #cfg.checkpoints, tostring(cfg.debug), tostring(cfg.markers)))
end

local function sectorsText(list)
    local out = {}
    for i, ms in ipairs(list or {}) do out[i] = string.format('%.1f', ms / 1000) end
    return '[' .. table.concat(out, ' | ') .. ']'
end

local function onLap(data)
    local d = jsonDecode(data)
    if not d then return end
    local last = d.sectors and d.sectors[#d.sectors]
    -- toast: lap time and the LAST sector only. 17 sectors in one toast was a wall (26/10 screenshot).
    -- The full sector list is in the chat line and in laps.log on the server.
    toast(string.format('%s  lap %d  %s   last sector %s   best %s', d.name, d.laps, M.fmt(d.lapMs),
          last and string.format('%.1f', last / 1000) or '-', M.fmt(d.bestMs)), 6)
    if d.pid == M.myPid then
        flash('LAP  ' .. M.fmt(d.lapMs), 4, true, beep('CountdownGo'))
        lapClock.hold = 4; lapClock.holdMsg = 'LAP  ' .. M.fmt(d.lapMs); lapClock.t = 0; lapClock.shown = 0
        realtime(lapClock.holdMsg)            -- now, not next frame
    end
end

local function onSplit(data)
    local d = jsonDecode(data)
    if not d then return end
    -- sector = since the previous checkpoint, split = since the line, speed = entering this sphere
    local sector = string.format('%.1f', (d.sectorMs or 0) / 1000)
    toast(string.format('S%d  %s s   split %s   %d km/h', d.cp - 1, sector, M.fmt(d.splitMs), d.kph or 0), 4)
    flash(string.format('S%d   %s', d.cp - 1, sector), 2.5, true)
end

local function onInfo(data)
    local d = jsonDecode(data)
    if not (d and d.msg) then return end
    dbg('server: ' .. d.msg)
    if d.msg == 'timing started' or d.msg == 'GO start' then
        lapClock.running = true; lapClock.t = 0
        if d.msg == 'timing started' then flash('TIMING STARTED', 1.5, false) end
    end
end

-- shared race start. Server counts down for everyone; at GO every own car still in the
-- start bubble starts its lap from the server's GO instant.
local function insideCp(veh, cp)
    local p = veh:getPosition()
    local dx, dy, dz = p.x - cp.x, p.y - cp.y, p.z - cp.z
    return (dx*dx + dy*dy + dz*dz) <= (cp.r * cp.r)
end

local function onCount(data)
    local d = jsonDecode(data)
    if not d then return end
    if not counting then
        -- countdown just began: remember which own cars are on the line, for the jump-start call
        local cp1 = cfg.checkpoints[1]
        for vid, _ in pairs(MPVehicleGE.getOwnMap()) do
            local veh, s = be:getObjectByID(vid), state[vid]
            if veh and s and cp1 then s.armed = insideCp(veh, cp1); s.jumpSent = false end
        end
    end
    counting = true
    toast(tostring(d.n), 1)
    local snd = (d.n <= 3) and beep('Countdown' .. (4 - d.n)) or beep('Countdown1')
    flash(tostring(d.n), 1.0, true, snd)
end

-- Called from vehicle Lua (kkLapVE.lua) the frame a horn comes on. Honk in the start
-- bubble before your first sector = "we're ready", and the server counts everyone down.
function M.onHorn(vid)
    if not MPVehicleGE.getOwnMap()[vid] then return end       -- BeamMP: only our own cars
    local veh, cp1 = be:getObjectByID(vid), cfg.checkpoints[1]
    local s = state[vid]
    if not (veh and cp1 and s) then return end
    if counting or s.nextCp > 2 or not insideCp(veh, cp1) then return end
    TriggerServerEvent('kkLap_arm', jsonEncode({ vid = vid }))
    dbg(string.format('vid %d honked on the line, asked for a countdown', vid))
end

-- Put our vehicle-side file into every car that spawns (BeamNG GE hook; BeamMP does the same).
local function loadVE(vid)
    local veh = be:getObjectByID(vid)
    if veh then veh:queueLuaCommand("extensions.loadModulesInDirectory('lua/vehicle/extensions/kkLap')") end
end
local function onVehicleSpawned(vid) loadVE(vid) end

local function onGo(data)
    counting = false
    toast('GO!', 2)
    flash('GO!', 2.0, true, beep('CountdownGo'))
    local cp1 = cfg.checkpoints[1]
    if not cp1 then return end
    for vid, _ in pairs(MPVehicleGE.getOwnMap()) do
        local veh = be:getObjectByID(vid)
        local s   = state[vid]
        if veh and s and insideCp(veh, cp1) then
            local vx, vy, vz = veh:getVelocityXYZ()
            TriggerServerEvent('kkLap_cross', jsonEncode({ vid = vid, cp = 1, go = true,
                kph = math.sqrt(vx*vx + vy*vy + vz*vz) * 3.6 }))
            s.nextCp  = (#cfg.checkpoints > 1) and 2 or 1
            s.inside  = true                 -- already in cp1: leaving it is not a crossing
            s.started = true
            s.flashCp = 1; s.flashT = 1.5
            s.armed   = false
            dbg(string.format('vid %d GO start', vid))
        end
    end
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

    -- running lap clock on screen. CLIENT-SIDE ESTIMATE from the moment the server said
    -- "timing started": the official time is the server's and arrives with the lap toast.
    if lapClock.running then
        lapClock.t = lapClock.t + dt
        lapClock.shown = lapClock.shown + dt
        if lapClock.hold > 0 then
            lapClock.hold = lapClock.hold - dt
            if lapClock.shown > 0.5 then lapClock.shown = 0; realtime(lapClock.holdMsg) end
        elseif lapClock.shown > 0.1 then
            lapClock.shown = 0; realtime(M.fmt(math.floor(lapClock.t * 1000)))
        end
    end

    local n = #cfg.checkpoints
    if n == 0 then return end

    -- BeamMP: table of the vehicles THIS client owns, keyed by game vehicle id.
    -- Split-screen = two entries. Remote players' cars are not in it.
    for vid, _ in pairs(MPVehicleGE.getOwnMap()) do
        local veh = be:getObjectByID(vid)             -- BeamNG: id -> object
        if veh then
            local s  = state[vid] or { nextCp = 1, inside = false, started = false, flashCp = nil, flashT = 0, armed = false }
            state[vid] = s
            local cp = cfg.checkpoints[s.nextCp]
            local p  = veh:getPosition()
            local dx, dy, dz = p.x - cp.x, p.y - cp.y, p.z - cp.z
            local inside = (dx*dx + dy*dy + dz*dz) <= (cp.r * cp.r)

            -- jump start: a car that was on the line when the countdown began and leaves the
            -- bubble before GO. Announced to everyone by the server. Shame is the penalty.
            if counting and s.armed and not s.jumpSent and not insideCp(veh, cfg.checkpoints[1]) then
                s.jumpSent = true
                TriggerServerEvent('kkLap_jump', jsonEncode({ vid = vid }))
            end

            if inside and not s.inside then
                -- edge: just entered the sphere. Report once.
                -- speed read straight off the car at the moment it enters the sphere.
                -- BeamNG: getVelocityXYZ returns three numbers in m/s (same call BeamMP's MPVehicleGE uses).
                local vx, vy, vz = veh:getVelocityXYZ()
                local kph = math.sqrt(vx*vx + vy*vy + vz*vz) * 3.6
                TriggerServerEvent('kkLap_cross', jsonEncode({ vid = vid, cp = s.nextCp, kph = kph, pos = { x = p.x, y = p.y, z = p.z } }))
                dbg(string.format('vid %d entered cp %d', vid, s.nextCp))
                s.started = true                   -- from now on, passed checkpoints are drawn as done
                s.flashCp = s.nextCp; s.flashT = 1.5   -- and this one flashes white for 1.5 s
                s.nextCp = (s.nextCp % n) + 1
                inside   = false   -- new target sphere: start outside it, or touching spheres get missed (bench, 26/10)
            end
            s.inside = inside
            if s.flashT > 0 then s.flashT = s.flashT - dt end
        end
    end
end

-- How checkpoint i should look for a car in state s. Pure, so the bench can test it.
--   'flash' just went through it,  'next' hit this one next,  'done' already through this lap,
--   'todo' not yet this lap.  Start/finish counts as done once you have passed any other checkpoint.
function M.cpStyle(i, s, n)
    if not s then return 'todo' end
    if s.flashCp == i and s.flashT > 0 then return 'flash' end
    if i == s.nextCp then return 'next' end
    if not s.started then return 'todo' end
    if s.nextCp == 1 then return 'done' end          -- every other cp passed, heading for the line
    if i < s.nextCp then return 'done' end
    return 'todo'
end

-- ----------------------------------------------------- per-frame drawing
-- BeamMP draws its name tags from onPreRender, not onUpdate, so that is where ours go too.
-- Each checkpoint is drawn as the exact sphere the timer uses, so what you see is what
-- triggers. Label floats above it. Your next checkpoint is blue, start/finish green, rest orange.
local function onPreRender(dt)
    if not draw.ok or not cfg.markers then return end
    local n = #cfg.checkpoints
    if n == 0 then return end

    local s                                       -- state of the car in seat 0, drives the colours
    local pv = be:getPlayerVehicle(0)
    if pv then s = state[pv:getID()] end

    for i, cp in ipairs(cfg.checkpoints) do
        local style = M.cpStyle(i, s, n)
        local base  = (i == 1) and 'START / FINISH' or ('CP ' .. i)
        local c, label
        if style == 'flash' then c = draw.flash; label = base .. '  OK'
        elseif style == 'next' then c = draw.next; label = '> ' .. base .. ' <'
        elseif style == 'done' then c = draw.done; label = base .. '  done'
        else c = (i == 1) and draw.start or draw.cp; label = base end
        draw.sphere(cp.x, cp.y, cp.z, cp.r, c, true)
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
    AddEventHandler('kkLap_count',  onCount)
    AddEventHandler('kkLap_go',     onGo)
    for vid, _ in pairs(MPVehicleGE.getOwnMap()) do loadVE(vid) end   -- cars that spawned before we loaded
    dbg('extension v' .. M.VERSION .. ' loaded, markers ' .. (draw.ok and 'available' or 'NOT available') .. ', asking server for config')
    TriggerServerEvent('kkLap_getcfg', '')   -- usually too early to arrive; onPlayerJoin on the server covers it (26/10)
end

M.onExtensionLoaded = onExtensionLoaded
M.onUpdate          = onUpdate
M.onPreRender       = onPreRender
M.onVehicleSpawned  = onVehicleSpawned
M.myPid             = nil     -- our player id, stamped by the server on the config it sends us
return M
