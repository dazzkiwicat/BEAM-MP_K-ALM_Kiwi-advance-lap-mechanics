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
M.VERSION = '0.26'   -- bump this; the zip name and the log lines follow it

local cfg      = { debug = true, markers = true, checkpoints = {} }
-- [vid] = { nextCp, inside, started, waiting, inStart, flashCp, flashT, lastPos, slewT, physResets, invalid }
-- waiting: in the start bubble with no lap running (v0.26: the bubble is a waiting area, the clock is
-- at zero there; honk -> countdown -> GO -> the first car out starts the clock, the server decides all of it)
local state    = {}
local buildLine, removeLine  -- defined further down, used by onCfg above them
local musicAutoCheck         -- defined in the music block, used by onVehicleSpawned above it
local musicRetry = { t = 0, tries = 0 }   -- autoplay retries (v0.22): every 3 s, at most 10, reset by each config
local tgt      = {}          -- the targets in order: checkpoints, then the separate finish line if there is one
local lastDbg  = 0
local lastTrackRev = nil     -- the server's cfg.trackRev we last reset our lap state for (v0.24)

local function dbg(msg)
    log('I', 'kkLap', '[KKLAP] ' .. msg)   -- 'I' not 'D': BeamMP itself only logs E/W/I
    if cfg.debug then TriggerServerEvent('kkLap_dbg', msg) end
end
-- a failure: logged as an error and ALWAYS sent to the server, debug on or off; the server logs
-- every line that starts "ERROR " (v0.25: so a broken HUD or onUpdate shows in kkLap.log)
local function dbgErr(msg)
    log('E', 'kkLap', '[KKLAP] ' .. msg)
    pcall(TriggerServerEvent, 'kkLap_dbg', 'ERROR ' .. msg)
end
local function ownList()                 -- "[100, 101]": the own map, for the log (v0.25)
    local ids = {}
    local ok, own = pcall(MPVehicleGE.getOwnMap)
    if not ok or type(own) ~= 'table' then return '[?]' end
    for vid in pairs(own) do ids[#ids + 1] = tonumber(vid) or vid end
    table.sort(ids, function(a, b) return tostring(a) < tostring(b) end)
    for i, v in ipairs(ids) do ids[i] = tostring(v) end
    return '[' .. table.concat(ids, ', ') .. ']'
end

-- kkLap's own HUD (v0.22): everything toast() and flash() show ALSO goes on this queue, and
-- drawHud() (further down) draws it with the game's imgui, no UI app needed. In split screen
-- (two narrow windows) the Race Countdown app and the chat app have no room: this does.
-- items: { text, ttl (seconds left), big (countdown / GO / WRONG WAY / sector flash) }
local hud = { items = {}, bigMode = nil, broken = false }
-- three start lights (v0.26, Darren: "3 lights to go"), drawn in the HUD window during the countdown:
-- 5 4 all dark, 3 one red, 2 two red, 1 three red, GO three green for 2 s, cancel = gone
local lights = { on = false, lit = 0, green = false, ttl = 0 }
local function hudPush(text, ttl, big)
    text = tostring(text); ttl = tonumber(ttl) or 3; big = big and true or false
    for i = #hud.items, 1, -1 do
        local it = hud.items[i]
        if it.text == text then                     -- toast + flash of the same words = one item
            table.remove(hud.items, i)
            ttl = math.max(ttl, it.ttl); big = big or it.big
        end
    end
    if big then                                     -- a new big line replaces the old one, like the flash app;
        for i = #hud.items, 1, -1 do                -- else an older lap flash shows through between two digits (bench)
            if hud.items[i].big then table.remove(hud.items, i) end
        end
    end
    table.insert(hud.items, { text = text, ttl = ttl, big = big })
    while #hud.items > 12 do table.remove(hud.items, 1) end
end
local function hudTick(dt)
    for i = #hud.items, 1, -1 do
        local it = hud.items[i]
        it.ttl = it.ttl - dt
        if it.ttl <= 0 then table.remove(hud.items, i) end
    end
    if lights.on and lights.green then          -- the green lights go out 2 s after GO
        lights.ttl = lights.ttl - dt
        if lights.ttl <= 0 then lights.on = false; lights.green = false; lights.lit = 0 end
    end
end
M.hudState = hud          -- for the bench
M.lightsState = lights    -- for the bench

-- ImGui's Text, TextWrapped and TextColored are printf-style: a '%' in a rule line or a player's
-- name ("%s") read arguments that are not there (review, v0.24). TextUnformatted (BeamNG's stock
-- gui.lua uses it) takes the string as it is; probed once, like the big-text method. The
-- printf-style calls that are left get every % doubled, which printf prints as one.
local function imEsc(t) return (tostring(t):gsub('%%', '%%%%')) end
local function imText(im, t)
    if hud.unformatted == nil then
        hud.unformatted = pcall(function() assert(type(im.TextUnformatted) == 'function') end)
        dbg('imgui plain text uses ' .. (hud.unformatted and 'TextUnformatted' or 'Text, percent signs doubled'))
    end
    if hud.unformatted then im.TextUnformatted(tostring(t)) else im.Text(imEsc(t)) end
end

local function toast(msg, ttl)
    -- BeamNG UI message, small, top left. Same call BeamMP's own MPVehicleGE.lua uses.
    guihooks.trigger('Message', { ttl = ttl or 5, msg = msg, icon = 'timer' })
    hudPush(msg, ttl or 5, false)
end

-- Big centred flash, the one the game's own scenarios use for 3-2-1-GO and lap times.
-- Needs the "Race Countdown" UI app on the player's layout (docs.beammp.com/beamng/lua-snippets).
-- Without it nothing shows and nothing breaks, which is why every flash also sends a toast.
-- sound: a Lua string the UI runs, e.g. Engine.Audio.playOnce('AudioGui', 'event:UI_CountdownGo'); 0 = none.
local function flash(msg, ttl, big, sound)
    guihooks.trigger('ScenarioFlashMessage', { { msg, ttl or 2, sound or 0, big ~= false } })
    hudPush(msg, ttl or 2, big ~= false)
end
local function beep(name) return "Engine.Audio.playOnce('AudioGui', 'event:UI_" .. name .. "')" end

-- Mid-size persistent centre text: the running lap clock. Needs the "Race Realtime Display" UI app.
-- Since v0.23 this is only the FALLBACK path (imgui HUD switched off with /hud off, or broken):
-- the normal clock is the kkLapTimer imgui window (drawTimer, further down). Never both at once.
local function realtime(msg) guihooks.trigger('ScenarioRealtimeDisplay', { msg = msg }) end

-- Per-car lap clocks (v0.23), keyed by game vehicle id: split screen = two cars, two clocks.
-- CLIENT-SIDE ESTIMATE from the moment the server said "timing started" / "GO start"; the official
-- time is the server's and arrives with kkLap_lap.
-- [vid] = { running, t (s), hold (s left of the LAP line), holdMsg, stopAfterHold, atLine, goLine,
--           info = { line, gap = { text, ttl, slower } | nil, split, ttl } | nil, best (ms), laps }
-- atLine (v0.26): in the start bubble with nothing running, or a GO starter waiting for the first car
-- out: the window shows 0:00.000. info (v0.26): the last sector, its speed, gap and split, 5 s.
local clocks = {}
local function clockFor(vid)
    vid = tonumber(vid)
    if not vid then return nil end
    local c = clocks[vid]
    if not c then
        c = { running = false, t = 0, hold = 0, holdMsg = nil, stopAfterHold = false, atLine = false, goLine = false, info = nil, best = nil, laps = nil }
        clocks[vid] = c
    end
    return c
end
-- The gap line: negative = faster than the compared lap/sector, the same sign the server's deltaMs uses.
local function gapItem(ms)
    ms = tonumber(ms)
    if not ms then return nil end                    -- no reference yet (first lap): nothing shown
    return { text = (ms > 0 and '+' or '-') .. string.format('%.2f', math.abs(ms) / 1000), ttl = 3, slower = ms > 0 }
end
-- Which path shows the clock and the deltas: the imgui window, or the v0.22 UI-app + toast path.
local function hudTimerOn() return cfg.hud ~= false and not hud.broken and ui_imgui ~= nil end

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
    draw.black = color(0, 0, 0, math.min(255, a * 5))         -- chequered band round the start line
    draw.white = color(255, 255, 255, math.min(255, a * 5))
    draw.fg    = color(255, 255, 255, 254)                    -- 254 not 255: BeamMP notes 255 breaks the bg alpha
    draw.bg    = color(0, 0, 0, 160)
end
do
    local ok = pcall(function()
        draw.sphere = ffi.C.BNG_DBG_DRAW_Sphere
        draw.text   = ffi.C.BNG_DBG_DRAW_TextAdvanced
        draw.prism  = ffi.C.BNG_DBG_DRAW_SquarePrism   -- (x1,y1,z1, x2,y2,z2, baseH,baseW, tipH,tipW, colour, useZ); 2 cm high = flat
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
    if type(cfg.checkpoints) ~= 'table' then cfg.checkpoints = {} end
    -- v0.24: only a NEW track revision (a track change, /reset, a server (re)load) drops the lap state.
    -- Any other push (/hud, /set, /music ...) keeps every lap running: before, it wiped the client's
    -- lap while the server kept its run, and the next line crossing was "wrong cp" (review, 30/10).
    local newTrack = (d.trackRev == nil) or (d.trackRev ~= lastTrackRev)   -- nil: a pre-0.24 server, always reset
    local why                                      -- v0.25: the log says why the lap state went or stayed
    if d.trackRev == nil then why = 'no track rev (pre-0.24 server): lap state reset'
    elseif lastTrackRev == nil then why = 'first config: lap state reset'
    elseif newTrack then why = string.format('track changed (rev %s -> %s): lap state reset', tostring(lastTrackRev), tostring(d.trackRev))
    else why = string.format('same track (rev %s): lap state kept', tostring(d.trackRev)) end
    lastTrackRev = d.trackRev
    if newTrack then
        for vid in pairs(clocks) do clocks[vid] = nil end; realtime('')   -- track changed or reset: clocks off
        state = {}                                    -- checkpoints changed, start fresh
        lights.on = false; lights.green = false; lights.lit = 0   -- a countdown from before the change is over (the server re-sends a live one each second)
    end
    if cfg.lineStyle == nil then cfg.lineStyle = 'paint' end
    musicRetry.t, musicRetry.tries = 0, 0   -- a new config = a fresh set of autoplay retries
    musicAutoCheck()      -- starts, keeps, or clears the autoplay track (music block below)
    tgt = {}
    for i, c in ipairs(cfg.checkpoints) do tgt[i] = c end
    if cfg.finish then tgt[#tgt + 1] = cfg.finish end     -- point-to-point: finish is the last target
    buildLine()
    if draw.ok then buildColours(cfg.markerAlpha) end   -- /lap set markerAlpha takes effect here
    dbg(string.format('cfg received: %d checkpoints%s, debug=%s, markers=%s, hud=%s, track rev %s, %s',
        #cfg.checkpoints, cfg.finish and ' + finish' or '', tostring(cfg.debug), tostring(cfg.markers), tostring(cfg.hud), tostring(d.trackRev), why))
    dbg('own map at cfg time: ' .. ownList())
    -- v0.25: tell the server which version got it (it warns a player on an old zip)
    TriggerServerEvent('kkLap_ack', jsonEncode({ v = M.VERSION, rev = d.trackRev, cps = #cfg.checkpoints, reset = newTrack }))
end

local function insideCp(veh, cp)
    local p = veh:getPosition()
    local dx, dy, dz = p.x - cp.x, p.y - cp.y, p.z - cp.z
    return (dx*dx + dy*dy + dz*dz) <= (cp.r * cp.r)
end
-- a new lap starts here (line, restart, GO): a teleport still waiting to be judged happened
-- before it, so it is not this lap's (v0.24; else a car put on the line by a teleport lost its first lap)
local function slewDone(s, p)
    s.slewT = 0
    if p then s.lastPos = { x = p.x, y = p.y, z = p.z } end
end
-- the timer window's info line: "S2  31.4 s   187 km/h" (sector number, sector time, speed into the sphere)
local function sectorLine(idx, sectorMs, kph)
    return string.format('S%d  %.1f s   %d km/h', idx, (tonumber(sectorMs) or 0) / 1000, math.floor(tonumber(kph) or 0))
end

local function sectorsText(list)
    local out = {}
    for i, ms in ipairs(list or {}) do out[i] = string.format('%.1f', ms / 1000) end
    return '[' .. table.concat(out, ' | ') .. ']'
end

local function onLap(data)
    local d = jsonDecode(data)
    if not d then return end
    local hudOn = hudTimerOn()
    if not hudOn then
        -- fallback (v0.22 path): toast with the lap time and the LAST sector only. 17 sectors in one
        -- toast was a wall (26/10 screenshot). The full list is in the chat line and laps.log.
        local last = d.sectors and d.sectors[#d.sectors]
        toast(string.format('%s  lap %d  %s   last sector %s   best %s', d.name, d.laps, M.fmt(d.lapMs),
              last and string.format('%.1f', last / 1000) or '-', M.fmt(d.bestMs)), 6)
    end
    -- v0.23: no lap toast on the HUD path, other drivers' laps are in chat (server side, unchanged)
    if d.pid ~= M.myPid then return end
    local c = clockFor(d.vid)
    if not c then return end
    c.best = tonumber(d.bestMs) or c.best           -- the server's r.best for this car's run
    c.laps = tonumber(d.laps) or c.laps
    c.running = true; c.t = 0; c.atLine = false; c.goLine = false
    c.hold = 4; c.holdMsg = 'LAP  ' .. M.fmt(d.lapMs)
    c.stopAfterHold = cfg.finish ~= nil              -- point-to-point: the run is over, the clock goes after the LAP line
    -- v0.26 (Darren: "it doesn't show stage time or speed"): the last sector, its speed and the lap's gap
    -- to the best go under the clock for 5 s, in the timer window, no toast
    local last = d.sectors and d.sectors[#d.sectors]
    c.info = { line = sectorLine(d.sectors and #d.sectors or 0, last, d.kph), gap = gapItem(d.deltaMs), split = nil, ttl = 5 }
    if hudOn then
        -- the sound used to ride on the lap flash; same Lua the UI would have run, straight from GE
        -- (BeamNG Engine.Audio.playOnce, as the music fallback uses). UNCHECKED that a UI event plays from GE.
        pcall(function() Engine.Audio.playOnce('AudioGui', 'event:UI_CountdownGo') end)
    else
        local delta = d.deltaMs and string.format('   %+.1f', d.deltaMs / 1000) or '   first lap'
        flash('LAP  ' .. M.fmt(d.lapMs) .. delta, 4, true, beep('CountdownGo'))
        realtime(c.holdMsg)                          -- now, not next frame
    end
end

local function onSplit(data)
    local d = jsonDecode(data)
    if not d then return end
    -- sector = since the previous checkpoint, split = since the line, speed = entering this sphere
    local sector = string.format('%.1f', (d.sectorMs or 0) / 1000)
    local delta  = d.deltaMs and string.format('   %+.1f', d.deltaMs / 1000) or ''    -- vs your best sector; "-0.3" = faster (v0.20)
    if hudTimerOn() then
        -- v0.23: the sector toast was the distraction (Darren, 30/10). v0.26: the information is back, in the
        -- timer window: sector time and speed (5 s), the gap to your best sector (3 s), the split under it.
        local c = clockFor(d.vid)
        if c then
            c.info = { line = sectorLine((tonumber(d.cp) or 1) - 1, d.sectorMs, d.kph), gap = gapItem(d.deltaMs),
                       split = 'split ' .. M.fmt(d.splitMs), ttl = 5 }
        end
        return
    end
    toast(string.format('S%d  %s s%s   split %s   %d km/h', d.cp - 1, sector, delta, M.fmt(d.splitMs), d.kph or 0), 4)
    flash(string.format('S%d   %s%s', d.cp - 1, sector, delta), 2.5, true)
end

local function onInfo(data)
    local d = jsonDecode(data)
    if not (d and d.msg) then return end
    dbg('server: ' .. d.msg)
    local vid = tonumber(d.vid)
    local c, s = clockFor(d.vid), vid and state[vid]
    if d.msg == 'at the line' then
        -- v0.26: in the start bubble with nothing to time (fresh, back mid-lap, or a cross the server would
        -- not time). Clock held at zero; the lap state follows the server: no lap, heading for nothing.
        if c then c.running = false; c.t = 0; c.hold = 0; c.holdMsg = nil; c.info = nil; c.stopAfterHold = false; c.atLine = true; c.goLine = false end
        if s then
            local veh, cp1 = be:getObjectByID(vid), cfg.checkpoints[1]
            s.started = false; s.nextCp = 1
            s.waiting = (veh and cp1 and insideCp(veh, cp1)) and true or false
            s.inside  = s.waiting                -- the target is the start sphere itself: no edge while inside it
        end
    elseif d.msg == 'GO start' then
        -- v0.26: the server named this car a starter of the race. Its clock stays at zero until the first
        -- car of the race drives out of the bubble ("timing started" then, for everyone in it).
        if c then c.running = false; c.t = 0; c.hold = 0; c.holdMsg = nil; c.info = nil; c.stopAfterHold = false; c.atLine = true; c.goLine = true end
        if s then
            s.nextCp = (#tgt > 1) and 2 or 1
            s.started = true; s.waiting = false
            s.invalid = false; s.physResets = 0
            s.flashCp = 1; s.flashT = 1.5
            local veh = be:getObjectByID(vid)
            if veh then slewDone(s, veh:getPosition()) end
            -- already in the start sphere: leaving it is not a crossing. With one sphere the target IS the start.
            s.inside = (veh and tgt[s.nextCp]) and insideCp(veh, tgt[s.nextCp]) or (s.nextCp == 1)
            dbg(string.format('vid %d GO start', vid))
        end
    elseif d.msg == 'timing started' then
        if c then c.running = true; c.t = 0; c.hold = 0; c.info = nil; c.stopAfterHold = false; c.atLine = false; c.goLine = false end
        flash('TIMING STARTED', 1.5, false)
    end
end


-- BeamMP's walking "unicycle" (the player on foot; MPVehicleGE.lua checks the same name) is an own
-- vehicle too: it must not be timed or counted in the start bubble (v0.24). getJBeamFilename: BeamNG GE.
local function isUnicycle(veh)
    local ok, j = pcall(function() return veh:getJBeamFilename() end)
    return ok and j == 'unicycle'
end

local function onCount(data)
    local d = jsonDecode(data)
    if not d then return end
    if d.cancel then                                 -- /race stop (v0.24): the countdown is off, honk again
        lights.on = false; lights.green = false; lights.lit = 0
        toast('countdown cancelled', 3)
        return
    end
    local n = tonumber(d.n)
    if not n then return end
    -- the lights: 5 and 4 all dark, 3 one red, 2 two red, 1 three red (v0.26)
    lights.on = true; lights.green = false; lights.ttl = 0
    lights.lit = (n <= 3) and math.max(0, math.min(3, 4 - n)) or 0
    toast(tostring(d.n), 1)
    local snd = (d.n <= 3) and beep('Countdown' .. (4 - d.n)) or beep('Countdown1')
    flash(tostring(d.n), 1.0, true, snd)
end

-- Called from vehicle Lua (kkLapVE.lua) the frame a horn comes on. Honk in the start bubble =
-- "we're ready", and the server counts everyone down. v0.26: every own honk is sent; the server
-- knows who is in its start bubble roster and whether a countdown runs, and refuses the rest.
function M.onHorn(vid)
    if not MPVehicleGE.getOwnMap()[vid] then return end       -- BeamMP: only our own cars
    TriggerServerEvent('kkLap_arm', jsonEncode({ vid = vid }))
    dbg(string.format('vid %d honked, asked the server for a countdown (it decides from its start bubble roster)', vid))
end

-- Put our vehicle-side file into every car that spawns (BeamNG GE hook; BeamMP does the same).
local function loadVE(vid)
    local veh = be:getObjectByID(vid)
    if veh then veh:queueLuaCommand("extensions.loadModulesInDirectory('lua/vehicle/extensions/kkLap')") end
end
local function onVehicleSpawned(vid)
    local own = MPVehicleGE.getOwnMap()[vid] ~= nil
    dbg(string.format('vehicle %s spawned (own: %s)', tostring(vid), own and 'yes' or 'no, or not yet: BeamMP lists it after the server round trip'))
    loadVE(vid); if own then musicAutoCheck() end
end

-- GO (v0.26): the server names the starters from its own start bubble roster and tells each car with
-- kkLap_info "GO start" (onInfo above). Here only the shared show: GO! and three green lights for 2 s.
local function onGo(data)
    toast('GO!', 2)
    flash('GO!', 2.0, true, beep('CountdownGo'))
    lights.on = true; lights.lit = 3; lights.green = true; lights.ttl = 2
end

-- music (v0.11). The server never plays anything: it tells every client, and each game plays
-- the file from the mod zip (art/sound/kkLap/<name>.ogg). Same calls RLS and BeamJoy use:
-- Engine.Audio.createSource(channel, path) -> id, then the source object plays/sets volume,
-- Engine.Audio.deleteSource(id) stops it. playOnce is the fallback (no stop, no volume change).
local music = { id = nil, src = nil, loop = false, path = nil, vol = 0.5, every = nil, t = 0, poll = 0 }
local function musicStop()
    if music.id then pcall(function() Engine.Audio.deleteSource(music.id) end) end
    music.id, music.src, music.loop, music.every, music.active = nil, nil, false, nil, false
end
-- loop (v0.14, DJ sets): once a second ask the source whether it is still playing and restart
-- it when it stops. If the game gives us no stoppable source (playOnce fallback), the server
-- can pass the track length in seconds and we re-fire playOnce on that clock.
local function musicTick(dt)
    if not music.loop then return end
    music.poll = music.poll + dt
    if music.poll < 1 then return end
    music.poll = 0
    if music.src then
        local ok, playing = pcall(function() return music.src:isPlaying() end)
        if ok and playing == false then
            dbg('music looped'); pcall(function() music.src:play(-1) end)
        end
    elseif music.every then
        music.t = music.t + 1
        if music.t >= music.every then
            music.t = 0; dbg('music looped (timed)')
            pcall(function() Engine.Audio.playOnce('AudioGui', music.path, { volume = music.vol }) end)
        end
    end
end
local function musicPath(name)
    -- mp3 plays too (RLS's player globs mp3/ogg/wav/flac through the same call): give the
    -- name with its extension, or without for .ogg
    local file = tostring(name)
    if not file:find('%.%w+$') then file = file .. '.ogg' end
    return 'art/sound/kkLap/' .. file
end
local musicPlay   -- defined below; used by onMusic and by the autoplay

-- autoplay (v0.21): the server's config carries musicAuto = the track every player should
-- hear; the admin's /music sets or clears it. Only (re)starts when the track actually
-- changed, since the config is pushed on every admin command.
-- v0.22: plays the moment the config arrives, car or no car. v0.21 waited for an own car,
-- but BeamMP adds a local car to the own map only after the server round trip
-- (MPVehicleGE.lua onServerVehicleSpawned), AFTER the GE onVehicleSpawned hook has fired, so
-- the first car was always missed and only a Ctrl+R started it (live, 30/10).
-- If the game refuses (audio not up yet), musicRetryTick tries again every 3 s, 10 times.
musicAutoCheck = function(retry)   -- retry: the retry line already said what it wants
    local want = cfg.musicAuto and musicPath(cfg.musicAuto) or nil
    if not want then
        if music.path and music.loop then musicStop(); dbg('music autoplay cleared (the server has no track: /music stop)') end
        return
    end
    -- already playing it, from the same /music command (musicNo: a new /music restarts it, v0.24, as the
    -- config is now the only message a looping /music sends)
    if music.path == want and music.active and music.no == cfg.musicNo then
        if not music.keptSaid then music.keptSaid = true; dbg('music: ' .. want .. ' already playing (same /music), kept') end
        return
    end
    music.keptSaid = nil
    if not retry then
        local own = ownList()
        dbg('music: want ' .. want .. (own == '[]' and ', no car yet (plays anyway)' or ', own cars ' .. own))
    end
    musicPlay(want, tonumber(cfg.musicVolume) or 0.5, true, tonumber(cfg.musicSeconds))
    music.no = cfg.musicNo
    dbg('music autoplay ' .. want .. (music.active and '' or ' (refused, will retry)'))
end
local function musicRetryTick(dt)
    if not cfg.musicAuto or music.active or musicRetry.tries >= 10 then return end
    musicRetry.t = musicRetry.t + dt
    if musicRetry.t < 3 then return end
    musicRetry.t = 0
    musicRetry.tries = musicRetry.tries + 1
    dbg(string.format('music autoplay retry %d of 10: want %s', musicRetry.tries, tostring(cfg.musicAuto)))
    musicAutoCheck(true)
    if not music.active and musicRetry.tries >= 10 then dbg('music autoplay gave up after 10 retries, waiting for the next config') end
end

local function onMusic(data)
    local d = jsonDecode(data)
    if not d then return end
    musicStop()
    if d.stop or not d.file then dbg('music stopped'); return end
    musicPlay(musicPath(d.file), tonumber(d.vol) or 0.5, d.loop ~= false, tonumber(d.seconds))
end

musicPlay = function(path, vol, loop, seconds)
    musicStop()
    local d = { loop = loop, seconds = seconds }
    music.path, music.vol, music.loop, music.every, music.t = path, vol, d.loop ~= false, tonumber(d.seconds), 0
    local ok, err = pcall(function()
        music.id  = Engine.Audio.createSource('AudioGui', path)
        music.src = music.id and scenetree.findObjectById(music.id)
        if not music.src then error('no source object for ' .. path) end
        music.src:setVolume(vol)                   -- UNCHECKED whether the source clamps at 1; playOnce path takes 2+ (RLS)
        music.src:play(-1)
    end)
    local how = 'via createSource'
    if not ok then
        how = 'via playOnce'
        dbg('music createSource failed (' .. tostring(err) .. '), trying playOnce')
        -- the source may exist even though the object lookup or setVolume failed: free it (v0.24, it leaked)
        if music.id then pcall(Engine.Audio.deleteSource, music.id) end
        music.id, music.src = nil, nil
        ok, err = pcall(function() Engine.Audio.playOnce('AudioGui', path, { volume = vol }) end)
    end
    music.active = ok and true or false            -- v0.22: true only if a play call worked, so autoplay retries
    dbg(string.format('music %s %s vol %.2f %s', ok and 'playing' or ('FAILED: ' .. tostring(err)), path, vol, ok and how or 'and playOnce failed too'))
end

-- server asked "where is your car" for /lap mark. Use the player's current vehicle.
-- v0.8.0: the reply also carries the car's heading (so a start line can run across the road)
-- and what the wheels are on. The surface lives in the car's own Lua, so we ask kkLapVE and
-- answer the server when it calls back (kkLap.onSurface), or after 1 s without an answer.
local pending = nil      -- { r, vid, t } while a /lap mark is waiting for the car's surface report

local function sendPosReply(sealed, surface)
    local req = pending; pending = nil
    if not req then return end
    local veh = be:getObjectByID(req.vid)
    if not veh then dbg('getpos: vehicle gone'); return end
    local p = veh:getPosition()
    local h = vec3(veh:getDirectionVector())       -- BeamNG; BeamMP wraps it the same way
    TriggerServerEvent('kkLap_posreply', jsonEncode({ r = req.r, what = req.what, pos = { x = p.x, y = p.y, z = p.z },
        heading = { x = h.x, y = h.y }, sealed = sealed, surface = surface }))
    dbg(string.format('pos sent %.1f %.1f %.1f heading %.2f %.2f surface %s sealed=%s', p.x, p.y, p.z, h.x, h.y, surface, tostring(sealed)))
end

local function onGetPos(data)
    local d   = jsonDecode(data) or {}
    local veh = be:getPlayerVehicle(0)             -- BeamNG: seat 0's vehicle
    if not veh then dbg('getpos: no player vehicle'); return end
    pending = { r = d.r, what = d.what, vid = veh:getID(), t = 0 }
    veh:queueLuaCommand('kkLapVE.reportSurface()')  -- BeamNG: run this in the car's Lua VM
end

-- called back from the car's Lua: kkLap.onSurface(id, sealed, "FL=ASPHALT,FR=ASPHALT,...")
function M.onSurface(vid, sealed, surface)
    if not pending or pending.vid ~= vid then return end
    sendPosReply(sealed == true, surface)
end

-- crossing the start line backwards (v0.20): the start stores the heading the car had at
-- /start; a car entering the bubble while moving against it is going the wrong way.
local function wrongWay(veh, cp)
    if not cp.hx then return false end
    local vx, vy = veh:getVelocityXYZ()
    if vx*vx + vy*vy < 1 then return false end        -- parked or crawling: not judged
    return (vx * cp.hx + vy * cp.hy) < 0
end

-- a lap that must not count: tell the server once, it does the announcing
function M.invalidate(vid, reason)
    local s = state[vid]
    if not s or not s.started or s.invalid then return end
    s.invalid = true
    s.slewT = 0
    TriggerServerEvent('kkLap_invalid', jsonEncode({ vid = vid, reason = reason }))
    dbg(string.format('vid %d lap invalid: %s', vid, reason))
    flash('LAP WILL NOT COUNT', 2, true)
end

-- called from the car's Lua (kkLapVE onReset) after ANY reset. Moved = position reset. Stayed = physics reset.
function M.onVehReset(vid)
    local s, veh = state[vid], be:getObjectByID(vid)
    if not (s and veh and s.lastPos) then return end
    local p = veh:getPosition()
    local dx, dy, dz = p.x - s.lastPos.x, p.y - s.lastPos.y, p.z - s.lastPos.z
    local moved = math.sqrt(dx*dx + dy*dy + dz*dz)
    s.slewT = 0
    s.lastPos.x, s.lastPos.y, s.lastPos.z = p.x, p.y, p.z
    if not s.started then return end
    if moved > 2 then
        M.invalidate(vid, string.format('position reset, moved %.0f m', moved))
    else
        s.physResets = (s.physResets or 0) + 1
        dbg(string.format('vid %d physics reset #%d counted', vid, s.physResets))
        toast(string.format('physics reset %d (counted)', s.physResets), 3)
    end
end

-- ----------------------------------------------------- rules window (v0.19)
-- Dear ImGui through the game's ui_imgui, the same binding BeamMP's own MPDebug.lua draws
-- its windows with (im.Begin / im.Text / im.Button / im.End from onUpdate). Opened by the
-- server on join and by /rules; the Close button or /rules again shuts it.
local rules = { open = false, lines = {}, player = {}, admin = nil }
local function onRules(data)
    local d = jsonDecode(data)
    if not d then return end
    rules.lines, rules.player, rules.admin = d.rules or {}, d.player or {}, d.admin
    if d.open == nil then rules.open = not rules.open else rules.open = d.open end
end
local function drawRules()
    if not rules.open then return end
    local im = ui_imgui
    if not im then return end
    local began = false                            -- a throw between Begin and End must still End (v0.24)
    local ok, err = pcall(function()
        im.SetNextWindowSize(im.ImVec2(560, 0), im.Cond_FirstUseEver)
        im.SetNextWindowBgAlpha(0.85)
        im.Begin('kkLap  v' .. M.VERSION .. '  -  rules and commands'); began = true
        im.TextColored(im.ImVec4(0.3, 1.0, 0.3, 1.0), 'RULES')
        -- TextWrapped is printf-style: imEsc. (TextUnformatted would not wrap in the 560 px window.)
        for _, l in ipairs(rules.lines) do im.TextWrapped(imEsc('- ' .. l)) end
        im.Separator()
        im.TextColored(im.ImVec4(0.3, 0.8, 1.0, 1.0), 'COMMANDS (type in chat)')
        for _, l in ipairs(rules.player) do im.TextWrapped(imEsc(l)) end
        if rules.admin then
            im.Separator()
            im.TextColored(im.ImVec4(1.0, 0.7, 0.3, 1.0), 'ADMIN')
            for _, l in ipairs(rules.admin) do im.TextWrapped(imEsc(l)) end
        end
        im.Separator()
        if im.Button('Close   (or type /rules)') then rules.open = false end
        im.End(); began = false
    end)
    if not ok then
        if began then pcall(im.End) end
        rules.open = false; dbgErr('rules window failed to draw, imgui call mismatch: ' .. tostring(err))
    end
end
M.rulesState = rules      -- for the bench

-- ----------------------------------------------------- on-screen HUD (v0.22)
-- One borderless, click-through window top centre, drawn with ui_imgui (the same binding
-- as the rules window above, which is live). Flag names, bit.bor, GetIO().DisplaySize and
-- SetNextWindowPos with a pivot are from the stock BeamNG gui.lua dump.
-- Big text: SetWindowFontScale(3) if the binding has it (UNCHECKED), else PushFont3 if it
-- exists (UNCHECKED, font name too), else plain text. Which one worked is logged once.
local function onHud(data)                      -- server race-start lines: "X ready (1 of 2)", "race start in 5", jumps
    local d = jsonDecode(data)
    if d and d.msg then hudPush(d.msg, 5, false) end
end
local function hudFlags(im)
    local f = bit.bor(im.WindowFlags_NoTitleBar, im.WindowFlags_NoResize, im.WindowFlags_NoMove,
                      im.WindowFlags_NoBackground, im.WindowFlags_NoInputs, im.WindowFlags_NoFocusOnAppearing)
    -- so the window grows to its text and stays centred; UNCHECKED name in BeamNG's binding, skipped if absent
    local ok, g = pcall(function() return bit.bor(f, im.WindowFlags_AlwaysAutoResize) end)
    return ok and g or f
end
local function hudColour(im, text)
    if text == 'WRONG WAY' or text:find('WILL NOT COUNT') then return im.ImVec4(1.0, 0.25, 0.2, 1.0) end
    if text == 'GO!' then return im.ImVec4(0.3, 1.0, 0.3, 1.0) end
    return im.ImVec4(1.0, 0.9, 0.3, 1.0)
end
-- scale: 3 = big, 2 = medium. Only the 'scale' mode has a medium; font3 and plain draw both the same.
local function hudText(im, text, colour, scale)
    if hud.bigMode == nil then
        if pcall(im.SetWindowFontScale, 1) then hud.bigMode = 'scale'
        elseif type(im.PushFont3) == 'function' then hud.bigMode = 'font3'
        else hud.bigMode = 'plain' end
        dbg('HUD big text uses ' .. hud.bigMode)
    end
    if hud.bigMode == 'scale' then
        im.SetWindowFontScale(scale or 3); im.TextColored(colour, imEsc(text)); im.SetWindowFontScale(1)
    elseif hud.bigMode == 'font3' and pcall(im.PushFont3, 'cairo_semibold_large') then
        im.TextColored(colour, imEsc(text)); im.PopFont()
    else
        im.TextColored(colour, imEsc(text))      -- TextColored is printf-style too (v0.24)
    end
end
local function hudBig(im, text) hudText(im, text, hudColour(im, text), 3) end
-- Three start lights under the big line (v0.26). Draw-list path first: GetWindowDrawList,
-- GetCursorScreenPos, ImDrawList_AddCircleFilled, GetColorU322, Dummy are from memory of BeamNG's
-- ui_imgui binding, UNCHECKED live, so the path runs in its own pcall; the first failure switches to
-- three 'O' at scale 3 for good (hud.lightsMode = 'text'). A lights failure never sets hud.broken.
local function lightColours(im)
    local cols = {}
    for i = 1, 3 do
        if lights.green then cols[i] = im.ImVec4(0.2, 1.0, 0.2, 1.0)
        elseif i <= lights.lit then cols[i] = im.ImVec4(1.0, 0.15, 0.15, 1.0)
        else cols[i] = im.ImVec4(0.25, 0.25, 0.25, 1.0) end
    end
    return cols
end
local function drawLights(im)
    if not lights.on then return end
    local cols = lightColours(im)
    if hud.lightsMode ~= 'text' then
        local ok, err = pcall(function()
            local dl = im.GetWindowDrawList()
            local p  = im.GetCursorScreenPos()
            for i = 1, 3 do
                im.ImDrawList_AddCircleFilled(dl, im.ImVec2(p.x + 40 + (i - 1) * 80, p.y + 36), 30, im.GetColorU322(cols[i]), 32)
            end
            im.Dummy(im.ImVec2(260, 76))             -- reserve the space the circles were drawn over
        end)
        if ok then return end
        hud.lightsMode = 'text'
        dbg('lights: draw list missing, text lights (' .. tostring(err) .. ')')
    end
    pcall(function()
        for i = 1, 3 do
            if i > 1 then im.SameLine() end          -- SameLine: ui_imgui, used by stock UI code (UNCHECKED here)
            hudText(im, 'O', cols[i], 3)
        end
    end)
    pcall(im.SetWindowFontScale, 1)                  -- a throw inside hudText may have left the big scale on
end
-- v0.25: the first time a kkLap window is drawn, the screen size it was drawn for (split screen
-- = half width) and the big-text method, once per session
local function hudFirstDraw(io, which)
    if hud.sizeSaid then return end
    hud.sizeSaid = true
    local ds = io and io.DisplaySize
    dbg(string.format('%s first draw: display %s x %s px, big text %s', which, tostring(ds and math.floor(ds.x) or '?'),
        tostring(ds and math.floor(ds.y) or '?'), tostring(hud.bigMode or 'not chosen yet (no big line so far)')))
end
local function drawHud()
    hud.bottom = nil                               -- where the HUD window ends this frame; nil = not drawn
    if cfg.hud == false or hud.broken or (#hud.items == 0 and not lights.on) then return end   -- the lights draw with no messages too (v0.26)
    local im = ui_imgui
    if not im then return end
    local big, small = nil, {}
    for i = #hud.items, 1, -1 do                   -- newest first
        local it = hud.items[i]
        if it.big then big = big or it
        elseif #small < 4 then table.insert(small, 1, it) end   -- up to 4, oldest on top
    end
    local began = false                            -- a throw between Begin and End must still End (v0.24)
    local ok, err = pcall(function()
        local io = im.GetIO()
        im.SetNextWindowPos(im.ImVec2(io.DisplaySize.x / 2, 40), im.Cond_Always, im.ImVec2(0.5, 0))
        im.Begin('##kkLapHud', nil, hudFlags(im)); began = true
        if big then hudBig(im, big.text) end
        drawLights(im)                                 -- the three start lights, under the big line (v0.26)
        for _, it in ipairs(small) do imText(im, it.text) end   -- player names can carry a % (v0.24)
        -- imgui.GetWindowHeight: used by BeamMP's chat.lua and the stock gui.lua. If the call is
        -- missing, a rough guess (16 px font) so the timer still sits below the messages.
        local okH, h = pcall(im.GetWindowHeight)
        if not (okH and type(h) == 'number') then h = (big and 56 or 0) + (lights.on and 76 or 0) + #small * 20 + 16 end
        hud.bottom = 40 + h
        im.End(); began = false
        hudFirstDraw(io, 'HUD')
    end)
    if not ok then
        if began then pcall(im.End) end
        hud.broken = true; dbgErr('HUD failed to draw, imgui call mismatch: ' .. tostring(err))
    end
end

-- ----------------------------------------------------- race timer (v0.23)
-- "The timer is small and hard to read and the toasts distract; place it like single-player time
-- trials" (Darren, 30/10). A second borderless imgui window, top centre, under the message HUD:
--   BIG     running lap time (or "LAP  m:ss.mmm" for 4 s after a lap; 0:00.000 while waiting in the bubble)
--   SMALL   while waiting: what to do ("honk to race" / "GO - drive out ...")   (v0.26)
--   MEDIUM  for 5 s after a split or lap: "S2  31.4 s   187 km/h", and on the same line the gap to your
--           best, red "+0.35" slower / green "-0.21" faster, for 3 s   (v0.26: Darren wanted the info back)
--   SMALL   "split m:ss.mmm" under it after a split
--   SMALL   "best m:ss.mmm   lap N" once this car has a lap
-- It follows the car the camera is on (be:getPlayerVehicle(0), BeamNG GE) if that car is ours and
-- timing, else the first own car that is timing. Hidden when no own car is timing.
-- HUD off (/hud off) or an imgui error: the v0.22 path (Race Realtime Display app + toasts) instead.
local function clockTick(dt)
    for _, c in pairs(clocks) do
        if c.running then
            c.t = c.t + dt
            if c.hold > 0 then
                c.hold = c.hold - dt
                if c.hold <= 0 and c.stopAfterHold then c.running = false end
            end
        end
        if c.info then                             -- the info line lives 5 s, the gap on it 3 s (as before)
            c.info.ttl = c.info.ttl - dt
            if c.info.gap then
                c.info.gap.ttl = c.info.gap.ttl - dt
                if c.info.gap.ttl <= 0 then c.info.gap = nil end
            end
            if c.info.ttl <= 0 then c.info = nil end
        end
    end
end
local function timerVid()
    local own = MPVehicleGE.getOwnMap()            -- BeamMP
    local pv  = be:getPlayerVehicle(0)            -- BeamNG GE: the car the camera is on
    local id  = pv and pv:getID()
    local function shown(c) return c.running or c.atLine end   -- a clock held at zero in the bubble counts (v0.26)
    if id and own[id] and clocks[id] and shown(clocks[id]) then return id end
    local first
    for vid, c in pairs(clocks) do
        if shown(c) and own[vid] and (not first or vid < first) then first = vid end
    end
    return first
end
M.timerVid = timerVid     -- for the bench
local timer = { path = nil, shown = 0, rt = '' }   -- rt: what the fallback last put on the UI app
local function drawTimer(c)
    local im = ui_imgui
    local begun = false
    local ok, err = pcall(function()
        local io = im.GetIO()
        local y  = hud.bottom and (hud.bottom + 40) or 90
        im.SetNextWindowPos(im.ImVec2(io.DisplaySize.x / 2, y), im.Cond_Always, im.ImVec2(0.5, 0))
        im.Begin('##kkLapTimer', nil, hudFlags(im)); begun = true
        local white = im.ImVec4(1.0, 1.0, 1.0, 1.0)
        if c.hold > 0 and c.holdMsg then hudText(im, c.holdMsg, im.ImVec4(1.0, 0.9, 0.3, 1.0), 3)
        else hudText(im, M.fmt(math.floor(c.t * 1000)), white, 3) end     -- 0:00.000 while held at the line
        if c.atLine and not c.running then
            imText(im, c.goLine and 'GO - drive out, the first car out starts the clock' or 'in the start bubble: honk to race')
        end
        if c.info then
            hudText(im, c.info.line, white, 2)
            if c.info.gap then
                pcall(im.SameLine)                   -- gap beside the sector line; on its own line if the call is missing
                hudText(im, c.info.gap.text, c.info.gap.slower and im.ImVec4(1, 0.35, 0.35, 1) or im.ImVec4(0.35, 1, 0.35, 1), 2)
            end
            if c.info.split then imText(im, c.info.split) end
        end
        if c.best then imText(im, 'best ' .. M.fmt(c.best) .. (c.laps and ('   lap ' .. tostring(c.laps)) or '')) end
        im.End(); begun = false
        hudFirstDraw(io, 'timer')
    end)
    if not ok then
        if begun then pcall(im.End) end
        hud.broken = true
        dbgErr('HUD timer failed to draw, imgui call mismatch: ' .. tostring(err))
    end
end
local function timerFrame(dt)
    local hudOn = hudTimerOn()
    local path  = hudOn and 'imgui window' or 'Race Realtime Display app + toasts'
    if path ~= timer.path then
        timer.path = path
        dbg('lap timer path: ' .. path)
        if hudOn and timer.rt ~= '' then realtime(''); timer.rt = '' end   -- never two clocks
    end
    local vid = timerVid()
    local c   = vid and clocks[vid]
    if hudOn then
        if c then drawTimer(c) end
        return
    end
    -- fallback, the v0.22 behaviour: the UI app's clock, refreshed every 0.1 s (LAP line every 0.5 s)
    timer.shown = timer.shown + dt
    local msg
    if not c then msg = ''
    elseif c.hold > 0 then if timer.shown > 0.5 then msg = c.holdMsg end
    elseif timer.shown > 0.1 then msg = M.fmt(math.floor(c.t * 1000)) end
    if msg and (msg ~= '' or timer.rt ~= '') then timer.shown = 0; timer.rt = msg; realtime(msg) end
end

-- ----------------------------------------------------- deleted / respawned cars (v0.22)
-- A car that is deleted (Ctrl+R = new game vehicle id, or removed) never runs the onUpdate
-- edge that says "left the start bubble", so the server kept it as a car that has not
-- honked and the countdown could never start (live, 30/10). Drop its state and, if it was
-- in the bubble, tell the server it left.
local function dropVeh(vid, why, verb)
    clocks[vid] = nil                              -- its lap clock goes with it (v0.23)
    local s = state[vid]
    if not s then
        if verb == 'destroyed' then dbg(string.format('vehicle %s destroyed (was in bubble: no, not one kkLap was timing)', tostring(vid))) end
        return
    end
    if s.inStart then TriggerServerEvent('kkLap_bubble', jsonEncode({ vid = vid, inside = false })) end   -- no kph: it did not drive out (v0.26)
    state[vid] = nil
    dbg(string.format('vehicle %s %s (%s, was in bubble: %s)', tostring(vid), verb or 'pruned', why,
        s.inStart and 'yes, told the server it left' or 'no'))
end
local pruneT = 0
local function pruneState(dt)                   -- once a second, not every frame
    pruneT = pruneT + dt
    if pruneT < 1 then return end
    pruneT = 0
    local own = MPVehicleGE.getOwnMap()            -- BeamMP
    for vid in pairs(state) do                     -- clearing a field during pairs is allowed in Lua
        if not be:getObjectByID(vid) then dropVeh(vid, 'vehicle gone')     -- BeamNG GE
        elseif not own[vid] then dropVeh(vid, 'no longer ours') end
    end
end
-- BeamNG GE hook when a vehicle is deleted; BeamMP's MPVehicleGE.lua hooks the same name.
local function onVehicleDestroyed(vid) dropVeh(vid, 'game hook', 'destroyed') end

-- ----------------------------------------------------- per-frame check
-- v0.25: the whole frame runs inside pcall (guarded, below). An error outside it stops the
-- extension for good with nothing on screen but "mod stopped working".
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
            dbg('alive, own vehicles: ' .. (#ids > 0 and table.concat(ids, ',') or 'none') .. ' (v' .. M.VERSION .. ')')   -- the server logs it once a minute, or when it changes
        end
    end

    clockTick(dt)          -- per-car lap clocks (v0.23); drawn by timerFrame below

    musicTick(dt)
    musicRetryTick(dt)
    drawRules()
    hudTick(dt)
    drawHud()
    timerFrame(dt)         -- after drawHud: it sits under the HUD window (hud.bottom)
    pruneState(dt)
    if pending then
        pending.t = pending.t + dt
        if pending.t > 1 then dbg('no surface report from the car in 1 s'); sendPosReply(false, 'UNKNOWN') end
    end

    local n = #tgt
    if n == 0 then return end
    -- a finish line with no start (an /undo or a hand-edited config.json): nothing to time, and every
    -- cfg.checkpoints[1] below would throw each frame (v0.24). Music, HUD and the sweep above still run.
    if not cfg.checkpoints[1] then return end

    -- BeamMP: table of the vehicles THIS client owns, keyed by game vehicle id.
    -- Split-screen = two entries. Remote players' cars are not in it.
    for vid, _ in pairs(MPVehicleGE.getOwnMap()) do
        local veh = be:getObjectByID(vid)             -- BeamNG: id -> object
        if veh and not isUnicycle(veh) then           -- on foot: not a racer, not in the bubble roster (v0.24)
            local s  = state[vid] or { nextCp = 1, inside = false, started = false, waiting = false, flashCp = nil, flashT = 0 }
            state[vid] = s
            local cp = tgt[s.nextCp]
            local p  = veh:getPosition()
            local dx, dy, dz = p.x - cp.x, p.y - cp.y, p.z - cp.z
            local inside = (dx*dx + dy*dy + dz*dz) <= (cp.r * cp.r)

            -- resets and teleports (v0.10). Physics reset (Insert) leaves the car where it is: allowed,
            -- counted. Anything that MOVES the car mid-lap (recover, teleport, map respawn) makes the
            -- lap not count. lastPos is only advanced by normal movement, so a jump stays visible to
            -- the reset callback that arrives a frame later (kkLap.onVehReset, from the car's Lua).
            if s.lastPos then
                local jx, jy, jz = p.x - s.lastPos.x, p.y - s.lastPos.y, p.z - s.lastPos.z
                local jump = math.sqrt(jx*jx + jy*jy + jz*jz)
                if jump > 100 * dt + 150 then
                    -- a backstop for teleports with no reset event. Coarse on purpose: recover and
                    -- respawn fire the reset callback and are judged there at 2 m.
                    -- Armed ONCE (v0.24): re-arming every frame meant a car that stayed where it was
                    -- teleported to never got judged at all (review, 30/10).
                    if not (s.slewT and s.slewT > 0) then s.slewT = 1.0; s.slewDist = jump end   -- wait 1 s for a reset event
                else
                    s.lastPos.x, s.lastPos.y, s.lastPos.z = p.x, p.y, p.z
                end
                if s.slewT and s.slewT > 0 then           -- counts down every frame, moved or not
                    s.slewT = s.slewT - dt
                    if s.slewT <= 0 then
                        M.invalidate(vid, string.format('moved %.0f m in one frame', s.slewDist or 0))   -- no-op before the first crossing
                        s.lastPos.x, s.lastPos.y, s.lastPos.z = p.x, p.y, p.z                     -- judged: the new spot is home
                    end
                end
            else
                s.lastPos = { x = p.x, y = p.y, z = p.z }
            end

            -- The start bubble is a waiting area (v0.26, Darren 30/10: "it should start at zero"). Driving
            -- INTO it with no lap to finish (a fresh car, or back mid-lap: Darren's rule 27/10, the partial
            -- lap is dropped) parks the clock at zero. From there only honk -> countdown -> GO -> the first
            -- car out starts the clock; the server runs all of that from the bubble enter/leave messages
            -- below. A car heading for the line with its lap running (nextCp 1, started) is finishing a
            -- lap: that entry is the normal crossing (edge test below) and the next lap starts at once.
            local cp1 = cfg.checkpoints[1]
            local inStart = insideCp(veh, cp1)
            if inStart and not s.inStart and not (s.nextCp == 1 and s.started) then
                if s.nextCp ~= 1 and wrongWay(veh, cp1) then
                    flash('WRONG WAY', 2, true); dbg(string.format('vid %d entered the start backwards, ignored', vid))
                else
                    local vx, vy, vz = veh:getVelocityXYZ()
                    local wasRunning = s.started          -- read before the send: the server's "at the line" answer may arrive inside it
                    TriggerServerEvent('kkLap_cross', jsonEncode({ vid = vid, cp = 1, restart = true,
                        kph = math.sqrt(vx*vx + vy*vy + vz*vz) * 3.6, pos = { x = p.x, y = p.y, z = p.z } }))
                    dbg(string.format('vid %d in the start bubble, waiting at the line%s', vid, wasRunning and ': partial lap dropped' or ''))
                    s.waiting = true; s.nextCp = 1; s.started = false
                    s.invalid = false; s.physResets = 0
                    s.flashCp = 1; s.flashT = 1.5
                    slewDone(s, p)
                    cp = tgt[1]; inside = true; s.inside = true    -- the target is the start sphere itself: no edge while it waits in it
                end
            elseif not inStart and s.inStart and s.waiting then
                -- drove out without a GO: nothing starts (honk only). During a countdown the server
                -- announces the jump start itself from the leave message below, so no line of ours then.
                s.waiting = false
                if not lights.on then flash('no timing: honk in the start bubble to race', 3, false) end
                local c = clocks[vid]
                if c and not c.running then c.atLine = false end   -- nothing to show: the window hides
                dbg(string.format('vid %d left the start bubble without a GO: not timed', vid))
            end
            if inStart ~= s.inStart then
                -- tell the server who is in the start bubble (v0.18). With the speed (v0.26): the first GO
                -- starter to drive out starts the race clock, and a jump start is judged there too.
                local vx, vy, vz = veh:getVelocityXYZ()
                TriggerServerEvent('kkLap_bubble', jsonEncode({ vid = vid, inside = inStart, kph = math.sqrt(vx*vx + vy*vy + vz*vz) * 3.6 }))
            end
            s.inStart = inStart

            if inside and not s.inside and s.nextCp == 1 and wrongWay(veh, cp) then
                flash('WRONG WAY', 2, true); dbg(string.format('vid %d crossed the start backwards, ignored', vid))
                s.inside = true; inside = true                -- swallow this entry; next proper entry counts
            elseif inside and not s.inside then
                -- edge: just entered the sphere. Report once.
                -- speed read straight off the car at the moment it enters the sphere.
                -- BeamNG: getVelocityXYZ returns three numbers in m/s (same call BeamMP's MPVehicleGE uses).
                local vx, vy, vz = veh:getVelocityXYZ()
                local kph = math.sqrt(vx*vx + vy*vy + vz*vz) * 3.6
                TriggerServerEvent('kkLap_cross', jsonEncode({ vid = vid, cp = s.nextCp, kph = kph, resets = s.physResets or 0, pos = { x = p.x, y = p.y, z = p.z } }))
                s.physResets = 0
                dbg(string.format('vid %d entered cp %d', vid, s.nextCp))
                s.started = true                   -- from now on, passed checkpoints are drawn as done
                s.flashCp = s.nextCp; s.flashT = 1.5   -- and this one flashes white for 1.5 s
                if s.nextCp == 1 or (cfg.finish and s.nextCp == n) then s.invalid = false; s.physResets = 0; slewDone(s, p) end   -- new lap: clean slate
                if cfg.finish and s.nextCp == n then s.started = false end   -- crossed the finish: course done, back to fresh
                s.nextCp = (s.nextCp % n) + 1
                -- new target sphere: start outside it, or touching spheres get missed (bench, 26/10).
                -- Not with ONE checkpoint: the target is the same sphere, and the car must leave it first,
                -- else it "crossed" again every frame it sat inside (v0.24).
                if n > 1 then inside = false end
            end
            s.inside = inside
            if s.flashT > 0 then s.flashT = s.flashT - dt end
        end
    end
end

-- One error message = one log line the first time (sent to the server too), then a count every
-- 10 s while it keeps happening ("onUpdate error x12: ..."). The frame after it runs as normal.
local frameErr = { counts = {}, said = {}, msg = {}, t = 0, kinds = 0 }
M.frameErrors = frameErr.counts              -- for the bench: ['onUpdate error: <msg>'] = count
local function guarded(where, fn, dt)
    frameErr.t = frameErr.t + (tonumber(dt) or 0)
    if frameErr.t >= 10 then                 -- the counts, every 10 s, only for errors that happened again
        frameErr.t = 0
        for k, n in pairs(frameErr.counts) do
            if frameErr.said[k] ~= n then frameErr.said[k] = n; dbg(string.format('%s error x%d: %s', frameErr.msg[k][1], n, frameErr.msg[k][2])) end
        end
    end
    local ok, err = pcall(fn, dt)
    if ok then return end
    err = tostring(err)
    local key = where .. ' error: ' .. err
    if not frameErr.counts[key] then
        if frameErr.kinds >= 20 then err = '(more than 20 different errors, the rest counted together)'; key = where .. ' error: ' .. err end
        if not frameErr.counts[key] then frameErr.kinds = frameErr.kinds + 1; frameErr.msg[key] = { where, err } end
    end
    local n = (frameErr.counts[key] or 0) + 1
    frameErr.counts[key] = n
    if n == 1 then frameErr.said[key] = 1; dbgErr(string.format('%s error x1: %s', where, err)) end
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

-- ----------------------------------------------------- the start/finish line itself
-- Two ways, cfg.lineStyle: "paint" = a real DecalRoad on the road with the game's chequered
-- start-line material (checkered_line, seen in RLS West Coast level files; UNCHECKED on every
-- level: if it shows as an orange warning texture, /lap line draw);  "draw" = flat chequer
-- of 2 cm high boxes drawn every frame with the FFI square prism, same family as the spheres.
-- Geometry from cp1: heading (hx,hy) recorded at /lap mark, line runs ACROSS the road.
local line = { objs = {}, cells = {} }
local function lineGeometry(cp)
    local fx, fy = cp.hx or 0, cp.hy or 1
    local len = math.sqrt(fx*fx + fy*fy); if len < 1e-6 then fx, fy, len = 0, 1, 1 end
    fx, fy = fx / len, fy / len                    -- forward, flat
    local rx, ry = fy, -fx                         -- right = forward x up
    local w = cfg.lineWidth or 10                  -- metres across the road
    local z = cp.z
    local ok, zs = pcall(function() return be:getSurfaceHeightBelow(vec3(cp.x, cp.y, cp.z + 2)) end)
    if ok and type(zs) == 'number' and zs > -1e5 and math.abs(zs - cp.z) < 3 then z = zs end
    return fx, fy, rx, ry, w, z
end

function removeLine()
    for _, o in ipairs(line.objs) do pcall(function() o:delete() end) end
    line.objs = {}; line.cells = {}
end

local function buildOneLine(cp, tag)
    local fx, fy, rx, ry, w, z = lineGeometry(cp)
    if cfg.lineStyle == 'paint' then
        -- stock GE pattern (util/procTrack.lua, editor/roadSpline/layerMgr.lua, 0.40 dump):
        -- createObject -> setField... -> registerObject -> insertNodeNoRegen x2 -> regenerate
        local ok, err = pcall(function()
            local r = createObject('DecalRoad')
            r:setField('material',       0, cfg.lineMaterial or 'checkered_line')
            r:setField('improvedSpline', 0, 'true')
            r:setField('overObjects',    0, 'true')      -- paints onto mesh roads too
            r:setField('textureLength',  0, '2')
            r:setField('renderPriority', 0, '1')         -- low = on top of the level's own roads
            r:setField('drivability',    0, '-1')        -- keep it out of the AI / minimap road graph
            r:setField('startEndFade',   0, '0 0')
            r:registerObject('kkLap_' .. tag .. 'line_' .. tostring(os.clock()):gsub('%.', '_'))
            r:insertNodeNoRegen(vec3(cp.x - rx * w/2, cp.y - ry * w/2, z), 1.0, 0)
            r:insertNodeNoRegen(vec3(cp.x + rx * w/2, cp.y + ry * w/2, z), 1.0, 1)
            r:regenerate()
            table.insert(line.objs, r)
        end)
        if ok then dbg(tag .. ' line painted (DecalRoad)'); return end
        dbg('paint failed, falling back to draw: ' .. tostring(err))
    end
    -- flat chequer: 2 rows x 10 cells, 1 m deep in total, drawn every frame from onPreRender
    local nA, nR, depth = 10, 2, 1.0
    local cw, rh = w / nA, depth / nR
    for r = 0, nR - 1 do
        for i = 0, nA - 1 do
            local ax = -w/2 + (i + 0.5) * cw          -- across
            local f0 = -depth/2 + r * rh              -- along
            local px, py = cp.x + rx * ax + fx * f0, cp.y + ry * ax + fy * f0
            local qx, qy = px + fx * rh, py + fy * rh
            table.insert(line.cells, { px, py, z + 0.03, qx, qy, z + 0.03, cw, ((i + r) % 2 == 0) })
        end
    end
    dbg(tag .. ' line drawn as flat cells, total ' .. #line.cells)
end

function buildLine()
    removeLine()
    if not cfg.markers then return end
    if cfg.checkpoints[1] then buildOneLine(cfg.checkpoints[1], 'start') end
    if cfg.finish then buildOneLine(cfg.finish, 'finish') end     -- its own chequered line too
end

-- ----------------------------------------------------- per-frame drawing
-- BeamMP draws its name tags from onPreRender, not onUpdate, so that is where ours go too.
-- Each checkpoint is drawn as the exact sphere the timer uses, so what you see is what
-- triggers. Label floats above it. Your next checkpoint is blue, start/finish green, rest orange.
local function onPreRender(dt)
    if not draw.ok or not cfg.markers then return end
    local n = #tgt
    if n == 0 then return end

    local s                                       -- state of the car in seat 0, drives the colours
    local pv = be:getPlayerVehicle(0)
    if pv then s = state[pv:getID()] end

    if draw.prism and #line.cells > 0 then
        for k = 1, #line.cells do
            local c = line.cells[k]
            draw.prism(c[1], c[2], c[3], c[4], c[5], c[6], 0.02, c[7], 0.02, c[7], c[8] and draw.white or draw.black, true)
        end
    end

    for i, cp in ipairs(tgt) do
        local style = M.cpStyle(i, s, n)
        local base  = (i == 1) and (cfg.finish and 'START' or 'START / FINISH') or ((cfg.finish and i == n) and 'FINISH' or ('CP ' .. i))
        local c, label
        if style == 'flash' then c = draw.flash; label = base .. '  OK'
        elseif style == 'next' then c = draw.next; label = '> ' .. base .. ' <'
        elseif style == 'done' and i ~= 1 then c = draw.done; label = base .. '  done'
        else c = (i == 1) and draw.start or draw.cp; label = base end   -- start/finish never fades (26/10)
        draw.sphere(cp.x, cp.y, cp.z, cp.r, c, true)
        -- v0.7.1 ringed the start sphere with 32 black/white balls: blocked the view of the road
        -- (Darren, 27/10). Removed. A flat painted line is the plan; see STATE.md.
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
    AddEventHandler('kkLap_music',  onMusic)
    AddEventHandler('kkLap_rules',  onRules)
    AddEventHandler('kkLap_hud',    onHud)
    for vid, _ in pairs(MPVehicleGE.getOwnMap()) do loadVE(vid) end   -- cars that spawned before we loaded
    dbg('extension v' .. M.VERSION .. ' loaded, markers ' .. (draw.ok and 'available' or 'NOT available') .. ', asking server for config')
    TriggerServerEvent('kkLap_getcfg', jsonEncode({ v = M.VERSION }))   -- usually too early to arrive; onPlayerJoin on the server covers it (26/10)
end

M.onExtensionLoaded = onExtensionLoaded
M.onUpdate          = function(dt) guarded('onUpdate', onUpdate, dt) end        -- v0.25: never dies silently
M.onPreRender       = function(dt) guarded('onPreRender', onPreRender, dt) end
M.onVehicleSpawned  = onVehicleSpawned
M.onVehicleDestroyed = onVehicleDestroyed
-- leaving: the music must stop AND stay stopped. v0.22's autoplay retry restarted it 3 s later
-- (review, v0.24): the retries are spent and the autoplay forgotten until the next config.
local function silence(why)
    local was = music.path
    removeLine(); musicStop()
    musicRetry.tries = 10
    cfg.musicAuto = nil
    if was then dbg('music: stopped (' .. why .. ')') end
end
M.onExtensionUnloaded = function() silence('extension unloaded') end
M.onClientEndMission  = function() silence('level exit') end   -- BeamNG: leaving the level: line gone anyway, music must stop (Darren, 27/10)
M.myPid             = nil     -- our player id, stamped by the server on the config it sends us
return M
