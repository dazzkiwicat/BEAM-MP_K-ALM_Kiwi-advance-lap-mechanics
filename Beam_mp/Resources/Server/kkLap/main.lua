-- kkLap : server plugin (BeamMP server Lua, v3.x API)
--
-- The server is the switchboard. It never looks at the world. It:
--   * holds the checkpoint list and the options (config.json beside this file)
--   * receives "I crossed checkpoint N" from clients and does the timing
--   * broadcasts results and config changes to every client
--   * answers /lap chat commands so options can be changed live, in game
--   * answers "lap ..." typed in the server console (Pterodactyl's Console tab): since v0.25
--     the admin commands run there only, unless cfg.adminChat is switched on
--
-- Every line this plugin prints starts with [KKLAP] so one grep finds it:
--     grep KKLAP Server.log
-- Client-side debug lines are forwarded here too (kkLap_dbg), so the server log
-- is the single place to read when something goes wrong.

local PLUGIN   = "kkLap"
local VERSION  = "0.26"   -- same number as the client zip it was built with
local CFG_PATH = "Resources/Server/kkLap/config.json"   -- forward slashes work on both OS

-- ---------------------------------------------------------------- logging
-- Every line goes to the console AND is appended to kkLap.log beside this file.
-- The console scrolls too fast to read; the file does not. Open it in the
-- Pterodactyl file manager, or:  tail -f Resources/Server/kkLap/kkLap.log
local LOG_PATH = "Resources/Server/kkLap/kkLap.log"
local TIMES_PATH = "Resources/Server/kkLap/times.json"   -- best lap per player, rewritten each lap
local LAPS_PATH  = "Resources/Server/kkLap/laps.log"     -- one line per lap, append only, never deleted
local function log(level, msg)
    local line = string.format("[KKLAP][%s] %s", level, tostring(msg))
    print(line)
    local f = io.open(LOG_PATH, "a")
    if f then f:write(os.date("%Y-%m-%d %H:%M:%S "), line, "\n"); f:close() end
end
local function dbg(msg)            -- only when config.debug is on
    if cfg and cfg.debug then log("D", msg) end
end
-- the same line at most once per `ms` per key; the repeats are counted and reported with the
-- next line that gets through (v0.25: say why, but do not flood the log)
local quiet = {}
local function logEvery(key, ms, level, msg, now)
    local q = quiet[key]
    if q and now - q.t < ms then q.n = q.n + 1; return end
    local extra = (q and q.n > 0) and string.format("  (+%d like it not logged)", q.n) or ""
    quiet[key] = { t = now, n = 0 }
    log(level, msg .. extra)
end

-- ---------------------------------------------------------------- config
-- Checkpoint: {x=, y=, z=, r=}  r is radius in metres.
-- Order matters: index 1 is start/finish, the rest are splits in lap order.
local defaultCfg = {
    debug        = true,     -- forward client debug lines and print server ones
    minLapMs     = 5000,     -- laps shorter than this are rejected (teleport, respawn)
    defaultRadius= 8,        -- metres, used by /lap mark when no radius given
    markers      = true,     -- every client draws the checkpoint spheres in the world (v0.4.0)
    markerAlpha  = 40,       -- marker transparency 0..255, /lap set markerAlpha 40 (v0.5.2)
    autoStart    = true,     -- a honk inside the start bubble starts a shared countdown (v0.6.0)
    countdown    = 5,        -- seconds
    lineStyle    = "paint",  -- start/finish line: "paint" = real road decal, "draw" = flat chequer (v0.8.0)
    lineWidth    = 10,       -- metres across the road
    minLaps      = 2,        -- laps a driver must complete before any lap of theirs counts for the board (v0.10)
    pointsMinDrivers = 3,    -- points only show once this many drivers are on the board
    djText       = "DJ set is in the Discord voice channel: ask in Discord for the invite",   -- /dj prints this (v0.13)
    admins       = {},       -- player names allowed to change anything. Empty = everyone (first /admin add locks it) (v0.19)
    adminChat    = false,    -- false: admin commands run from the server console only ("lap ..."), refused in chat (v0.25).
                             -- true: admins (above) may also type them in chat. Set from the console: lap adminchat on|off
    rulesOnJoin  = true,     -- open the rules window when a player joins
    rules        = {         -- shown in the in-game rules window, /rules add <text> | /rules clear
        "Park in the start bubble and honk once. Everyone in there must honk, then 5-4-3-2-1-GO.",
        "Someone parked and not honking? Honk twice to start without the rest.",
        "Arrive after GO? Your own honk starts your own countdown.",
        "Physics reset (Insert) is allowed and counted. Recover / teleport = that lap does not count.",
        "Back in the start bubble any time = fresh start, partial lap dropped.",
        "Two laps minimum before your best goes on the board. Points once 3 drivers have times.",
    },
    musicAuto    = nil,      -- track every player hears from the moment they join (v0.22; v0.21 waited for a car); set by /music <name>, cleared by /music stop
    musicVolume  = 0.5,      -- /lap music <name> plays art/sound/kkLap/<name>.ogg from the zip on every client (v0.11)
    hud          = true,     -- kkLap's own on-screen text (countdown, GO, ready lines), needs no UI app; /hud on|off (v0.22)
    announceLaps = true,     -- every lap goes to everyone in chat (v0.5.0)
    announceSplits = false,  -- every sector goes to everyone in chat too: noisy, off by default
    checkpoints  = {},
    finish       = nil,      -- a separate finish line {x,y,z,r,hx,hy} = point-to-point; nil = finish is the start (loop)
    trackRev     = 0,        -- +1 on every track change or /reset (v0.24): clients reset their lap state only when it moves
}
cfg = nil   -- global on purpose: other kkLap files (if any) can see it

local function loadCfg()            -- true = read from config.json
    local f = io.open(CFG_PATH, "r")
    if not f then
        cfg = defaultCfg
        log("I", "no config.json, using defaults")
        return
    end
    local txt = f:read("*a"); f:close()
    local ok, t = pcall(Util.JsonDecode, txt)
    if ok and type(t) == "table" then
        cfg = t
        -- fill any keys a hand-edited file left out
        for k, v in pairs(defaultCfg) do if cfg[k] == nil then cfg[k] = v end end
        log("I", "config loaded, " .. #cfg.checkpoints .. " checkpoints")
        return true
    else
        cfg = defaultCfg
        log("E", "config.json unreadable, using defaults: " .. tostring(t))
    end
end

local function saveCfg()
    local f, err = io.open(CFG_PATH, "w")
    if not f then log("E", "cannot write config: " .. tostring(err)); return end
    f:write(Util.JsonEncode(cfg)); f:close()
    dbg("config saved")
end

-- push the whole config to one player (-1 = everyone)
-- v0.24: a config push no longer wipes anyone's lap. Clients reset their lap state only when
-- cfg.trackRev moved (a track change or /reset, see trackChanged), and only then is the start
-- bubble roster cleared here: those clients re-report who is in it. Before, 14 commands pushed
-- the config, every client dropped its lap, and only 6 of them reset the server's runs (review, 30/10).
local lastBroadcastRev = nil
local function sendCfg(pid)
    if pid == -1 then
        if cfg.trackRev ~= lastBroadcastRev then
            local n = 0
            for _ in pairs(bubble) do n = n + 1 end
            if n > 0 then log("I", string.format("bubble cleared (track rev %s -> %s, %d cars): the clients re-report who is in it", tostring(lastBroadcastRev), tostring(cfg.trackRev), n)) end
            bubble = {}
        end
        lastBroadcastRev = cfg.trackRev
    end
    -- a direct send carries the receiver's own player id, so the client knows which laps are its own
    cfg.yourPid = (pid ~= -1) and pid or nil
    MP.TriggerClientEventJson(pid, "kkLap_cfg", cfg)
    cfg.yourPid = nil
    dbg("cfg sent to " .. pid)
end

-- ---------------------------------------------------------------- timing state
-- key = pid .. ":" .. vid   (split-screen = one pid, two vids, so vid is essential)
local runs  = {}
-- the track (or the timing) changed: every run on the server goes, and the new trackRev tells
-- every client to drop its lap state too, so both sides start from the line together (v0.24).
-- Callers: /mark, /start, /finish, /finish same, /undo, /clear, /reset, and a (re)load of this file.
local function trackChanged()
    cfg.trackRev = math.floor(tonumber(cfg.trackRev) or 0) + 1
    runs = {}
end
local timer = MP.CreateTimer()          -- server clock, seconds as float
local function nowMs() return math.floor(timer:GetCurrent() * 1000 + 0.5) end   -- round, not floor: 59.3 s * 1000 is 59299.999 in floating point (bench, 27/10)

local function fmt(ms)
    local m = math.floor(ms / 60000)
    local s = (ms % 60000) / 1000
    return string.format("%d:%06.3f", m, s)
end

local function say(pid, msg) MP.SendChatMessage(pid, "[LAP] " .. msg) end
-- race-start lines go to chat AND to every client's on-screen HUD (v0.22): in split screen
-- the chat app and the countdown app have no room, so chat alone is not enough. Only the
-- race-start lines use this; every other chat line stays chat only.
local function sayHud(msg)
    say(-1, msg)
    MP.TriggerClientEventJson(-1, "kkLap_hud", { msg = msg })
end

-- ---------------------------------------------------------------- events from clients
-- data = json {vid=number, cp=number, pos={x,y,z}}
-- ------------------------------------------------- leaderboard: best lap per player name
local board = {}          -- [name] = { bestMs, sectors = {...}, laps = n, kph = entry speed on best }
local function loadBoard()
    local f = io.open(TIMES_PATH, "r")
    if not f then return end
    local ok, t = pcall(Util.JsonDecode, f:read("*a")); f:close()
    if ok and type(t) == "table" then board = t end
end
local function saveBoard()
    local f = io.open(TIMES_PATH, "w")
    if f then f:write(Util.JsonEncode(board)); f:close() end
end
local function lapRecord(line)       -- the permanent record, one line per lap
    local f = io.open(LAPS_PATH, "a")
    if f then f:write(os.date("%Y-%m-%d %H:%M:%S "), line, "\n"); f:close() end
end
local function sec(ms) return string.format("%.1f", ms / 1000) end
local function sectorsText(list)
    local out = {}
    for i, ms in ipairs(list) do out[i] = sec(ms) end
    return "[" .. table.concat(out, " | ") .. "]"
end
-- who a driver IS: the BeamMP account id when there is one ("id:123456"), else the name.
-- Guest names (guest1234567) change every session; the id does not (v0.20).
local function playerKey(pid)
    local name = MP.GetPlayerName(pid) or ("p" .. pid)
    local ok, ids = pcall(MP.GetPlayerIdentifiers, pid)
    if ok and type(ids) == "table" and ids.beammp and ids.beammp ~= "" then return "id:" .. tostring(ids.beammp), name end
    return name, name
end
local function sortedBoard()
    local list = {}
    for key, e in pairs(board) do table.insert(list, { name = e.name or key, e = e }) end
    table.sort(list, function(a, b) return a.e.bestMs < b.e.bestMs end)
    return list
end

-- ------------------------------------------------- the lap clock (v0.26)
-- Darren, 30/10: "the timer starts when enter bubble, no, it should start at zero" and "make sure
-- the trigger for timer ONLY occurs when countdown has been activated via honk". So: the start
-- bubble is a waiting area, the clock sits at zero there. The sequence is honk -> countdown -> GO
-- -> the FIRST car of that race to drive out of the bubble starts the clock for every car of that
-- race, at that one instant. A car whose lap IS running and crosses the line starts the next lap at
-- once (drive-through). startLap is the ONLY code that sets r.lapStart to a time (the bench greps for it).
local function startLap(r, t, why)
    local n = #cfg.checkpoints + (cfg.finish and 1 or 0)
    r.lapStart  = t
    r.lastCp    = t
    r.sectors   = {}
    r.resets    = 0; r.invalid = false; r.invalidWhy = nil
    r.pendingGo = false
    r.nextCp    = (n > 1) and 2 or 1
    dbg(string.format("lap clock started for %s#%s at %d: %s", tostring(r.name), tostring(r.vid), t, why))
end
-- the run of one car (key pid:vid); made on first contact, its sector deltas start from the board's bests
local function runFor(pid, vid)
    local key = pid .. ":" .. tostring(vid)
    local r = runs[key]
    if not r then
        local e = board[playerKey(pid)]
        r = { nextCp = 1, lapStart = nil, lastCp = nil, best = nil, laps = 0, sectors = {}, resets = 0, invalid = false, pendingGo = false,
              bestSectors = (e and e.bestSectors) and e.bestSectors or {} }
        runs[key] = r
    end
    r.pid, r.vid, r.name = pid, vid, MP.GetPlayerName(pid) or ("p" .. pid)
    return r, key
end
-- the car is in the start bubble with nothing to time: clock at zero, "at the line" on its screen.
-- A partial lap (back into the bubble mid-lap) is dropped here: Darren's rule, 27/10.
local function atLine(r, pid, vid, why)
    if r.lapStart or r.pendingGo then dbg(string.format("at the line pid=%d vid=%s (%s)", pid, tostring(vid), why)) end
    r.lapStart = nil; r.lastCp = nil; r.sectors = {}; r.resets = 0; r.invalid = false; r.invalidWhy = nil
    r.raceNo = nil; r.pendingGo = false; r.nextCp = 1
    MP.TriggerClientEventJson(pid, "kkLap_info", { vid = vid, msg = "at the line" })
end
-- The first GO starter of a race to drive out of the start bubble starts the clock of EVERY car of that
-- race, at that instant: the slow car's lap includes its delay. Kept per race number, so a later group
-- (someone arriving after the first GO, honking, its own GO) runs its own race and never touches a car
-- that is already racing. Entries older than 20 races are forgotten at the next GO.
local raceStart = {}      -- [race.no] = ms the clock of that race started
local function raceOut(r, who, kph, t)
    local no = r.raceNo
    if not no then return end
    if not raceStart[no] then
        raceStart[no] = t
        log("I", string.format("race %d clock started by %s leaving the bubble at %d km/h", no, who, math.floor(tonumber(kph) or 0)))
    end
    for _, r2 in pairs(runs) do
        if r2.pendingGo and r2.raceNo == no then
            startLap(r2, raceStart[no], string.format("race %d, first car out", no))
            MP.TriggerClientEventJson(r2.pid, "kkLap_info", { vid = r2.vid, msg = "timing started" })
        end
    end
end

-- ------------------------------------------------- race start: shared countdown, one GO for all
local race = { active = false, left = 0, tGo = nil, no = 0 }
-- who is in the start bubble right now, and who has honked "ready" (v0.18, Darren's rule):
-- the countdown starts when EVERY car in the bubble has honked. One car alone: its honk is
-- enough. A car that arrives after GO is a new group: its own honk, its own countdown.
bubble = {}         -- key pid:vid -> { pid, vid, name, ready, started }  (global: sendCfg above clears it)
local function bubbleCounts()
    local waiting, ready = 0, 0
    for _, e in pairs(bubble) do
        if not e.started then waiting = waiting + 1; if e.ready then ready = ready + 1 end end
    end
    return waiting, ready
end
-- v0.25 logging: every car is "name#vid" in the log, and the roster says who the decision looked at
local function tag(e) return tostring(e.name) .. "#" .. tostring(e.vid) end
local function bubbleList()                    -- entries in a fixed order, so two log lines compare
    local keys = {}
    for k in pairs(bubble) do keys[#keys + 1] = k end
    table.sort(keys)
    local out = {}
    for i, k in ipairs(keys) do out[i] = bubble[k] end
    return out
end
local function bubbleSize() return #bubbleList() end
-- "[Darren#100 ready, Bob#7 waiting] (+1 already started)", and the names still to honk
local function roster()
    local parts, waitingOn, started = {}, {}, 0
    for _, e in ipairs(bubbleList()) do
        if e.started then started = started + 1
        else
            parts[#parts + 1] = tag(e) .. (e.ready and " ready" or " waiting")
            if not e.ready then waitingOn[#waitingOn + 1] = tag(e) end
        end
    end
    return "[" .. table.concat(parts, ", ") .. "]" .. (started > 0 and string.format(" (+%d already started)", started) or ""), waitingOn
end
-- true when a countdown started; false and the reason when it did not (v0.25: it used to return silently)
local function startCountdown(who)
    if race.active then return false, string.format("a countdown is already running (%d left)", race.left) end
    if race.tGo and nowMs() - race.tGo < 10000 then       -- just started one, ignore the parked cars
        return false, string.format("GO was %.1f s ago, the start ignores honks for 10 s after a GO", (nowMs() - race.tGo) / 1000)
    end
    if #cfg.checkpoints == 0 then return false, "no start line yet (lap mark <player> in the console)" end
    -- clamped and an integer BEFORE race.active goes true: /set countdown 1e999 made the %d below
    -- throw with race.active stuck true, and no countdown could ever start again (review, v0.24)
    local left = math.floor(math.max(1, math.min(60, tonumber(cfg.countdown) or 5)))
    race.active = true
    race.left   = left
    sayHud(string.format("race start in %d  (%s)  - sit in the start bubble", race.left, who))
    MP.TriggerClientEventJson(-1, "kkLap_count", { n = race.left })
    MP.CreateEventTimer("kkLap_tick", 1000)
    log("I", "countdown started by " .. who)
    return true
end
function onTick()
    if not race.active then MP.CancelEventTimer("kkLap_tick"); return end
    race.left = race.left - 1
    if race.left > 0 then
        say(-1, tostring(race.left))
        MP.TriggerClientEventJson(-1, "kkLap_count", { n = race.left })
    else
        MP.CancelEventTimer("kkLap_tick")
        race.active = false
        race.tGo    = nowMs()                 -- the GO instant: honks are ignored for 10 s after it. The lap clock starts when the first car drives out (v0.26)
        race.no     = race.no + 1
        raceStart[race.no] = nil; raceStart[race.no - 20] = nil   -- this race has no start instant yet; races older than 20 are forgotten
        local n = #cfg.checkpoints + (cfg.finish and 1 or 0)
        say(-1, "GO!")
        MP.TriggerClientEventJson(-1, "kkLap_go", { t = race.tGo })
        local gone = {}
        for _, e in ipairs(bubbleList()) do             -- this group has gone; late arrivals form the next
            if not e.started then
                e.started = true; e.ready = false; e.goNo = race.no; gone[#gone + 1] = tag(e)   -- goNo: in the bubble at THIS GO (v0.24)
                -- v0.26: the server names the starters from its own roster; the client sends nothing at GO.
                -- The run waits (pendingGo) with its clock at zero until the first car of this race drives out.
                local r = runFor(e.pid, e.vid)
                r.lapStart = nil; r.lastCp = nil; r.sectors = {}; r.resets = 0; r.invalid = false; r.invalidWhy = nil
                r.pendingGo = true; r.raceNo = race.no; r.nextCp = (n > 1) and 2 or 1
                MP.TriggerClientEventJson(e.pid, "kkLap_info", { vid = e.vid, msg = "GO start" })
            end
        end
        log("I", string.format("GO at %d, race %d, in the bubble: %s", race.tGo, race.no, #gone > 0 and table.concat(gone, ", ") or "nobody"))
    end
end
-- a car left the bubble (drove out, was deleted, its player left): if everyone still waiting
-- has honked, that is the countdown (v0.22). Before this a car that vanished without saying
-- so blocked the start for good, and the owner had to leave and rejoin (live, 30/10).
local function bubbleLeft()
    if not cfg.autoStart or race.active then return end
    local waiting, ready = bubbleCounts()
    if waiting > 0 and ready >= waiting then
        local who
        for _, e in ipairs(bubbleList()) do if e.ready and not e.started then who = e.name; break end end
        local ok, why = startCountdown((who or "?") .. ", last car left")
        log("I", string.format("bubble: %d of %d waiting cars ready after a car left %s -> %s", ready, waiting, (roster()), ok and "countdown" or ("no countdown: " .. why)))
    end
end
function onBubble(pid, data)                   -- client: "my car entered / left the start bubble"
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or type(d) ~= "table" then log("E", "bad bubble payload from " .. pid .. ": " .. tostring(data)); return end
    local key = pid .. ":" .. tostring(d.vid)
    if d.inside then
        if not bubble[key] then
            bubble[key] = { pid = pid, vid = d.vid, name = MP.GetPlayerName(pid) or ("p" .. pid), ready = false, started = false }
            log("I", string.format("bubble +%s (now %d)", tag(bubble[key]), bubbleSize()))
        end
    else
        local e = bubble[key]
        bubble[key] = nil
        if e then
            log("I", string.format("bubble -%s (now %d)", tag(e), bubbleSize()))
            -- v0.26: the server judges the whole start sequence from this one event.
            -- A ready (honked) car leaving while the countdown runs = jump start. Shame is the penalty.
            if race.active and e.ready and not e.started then
                sayHud(e.name .. " jumped the start")
                log("I", string.format("%s jumped the start (%d s left)", tag(e), race.left or 0))
            end
            -- A GO starter driving out: the clock of its race starts, for every car in it (first one out).
            -- A car that vanished (deleted, pruned: no speed in the payload) did not drive out.
            local r = runs[key]
            if r and r.pendingGo then
                if d.kph ~= nil then raceOut(r, tag(e), d.kph, nowMs())
                else r.pendingGo = false; log("I", string.format("%s left the bubble without driving (deleted or pruned): no race clock from it", tag(e))) end
            end
            bubbleLeft()
        end
    end
end

function onArm(pid, data)                      -- client: "my car honked inside the start bubble"
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or type(d) ~= "table" then log("E", "bad arm payload from " .. pid .. ": " .. tostring(data)); return end
    local key  = pid .. ":" .. tostring(d.vid)
    local name = MP.GetPlayerName(pid) or ("p" .. pid)
    local who  = name .. "#" .. tostring(d.vid)
    if not cfg.autoStart then log("I", "arm " .. who .. " ignored: autostart is off (lap autostart on)"); return end
    if race.active then log("I", string.format("arm %s ignored: a countdown is already running (%d left)", who, race.left)); return end
    local e = bubble[key]
    if not e then
        -- v0.26: the client sends every honk; only a car the server has in the start bubble counts
        log("I", string.format("arm %s refused: not in the start bubble %s", who, (roster())))
        return
    end
    local wasReady = e.ready == true             -- this is its second honk (v0.22)
    e.ready = true; e.started = false
    local waiting, ready = bubbleCounts()
    local list, waitingOn = roster()
    local line = string.format("arm %s: %d of %d ready %s", who, ready, waiting, list)
    local started, why
    if ready >= waiting then
        started, why = startCountdown(name .. (waiting > 1 and string.format(", all %d ready", waiting) or ""))
    elseif wasReady then
        -- "everyone honks" stays the rule; a second honk is the way out when someone sits in
        -- the bubble and never honks (Darren, 30/10). Cars inside at GO still get the GO start.
        sayHud(name .. " honked twice: starting without the rest")
        started, why = startCountdown(name)
        line = line .. " (second honk: not waiting for " .. table.concat(waitingOn, ", ") .. ")"
    else
        sayHud(string.format("%s ready  (%d of %d in the start bubble, honk when ready, twice to go without the rest)", name, ready, waiting))
        why = "waiting on " .. table.concat(waitingOn, ", ")
    end
    log("I", line .. (started and " -> countdown" or ""))
    if not started then log("I", "no countdown: " .. why) end
end

-- points: 100 for the top time, less the further off it you are. floor(100 * top / yours).
-- 10% slower = 90 points. Only shown once cfg.pointsMinDrivers drivers have times (Darren, 27/10).
function pointsFor(bestMs, topMs)
    if not bestMs or not topMs or bestMs <= 0 then return 0 end
    return math.floor(100 * topMs / bestMs)
end

-- client says the lap in progress must not count (position reset, teleport)
function onInvalid(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or type(d) ~= "table" then return end
    local key = pid .. ":" .. tostring(d.vid)
    local r = runs[key]
    local name = MP.GetPlayerName(pid) or ("p" .. pid)
    if not r or not r.lapStart then
        log("I", string.format("invalid from %s#%s ignored: no lap running on the server (%s): %s", name, tostring(d.vid), r and "run, no lap start" or "no run", tostring(d.reason)))
        return
    end
    r.invalid = true; r.invalidWhy = tostring(d.reason)
    say(-1, name .. "  lap will not count: " .. tostring(d.reason))
    log("I", string.format("%s#%s lap invalidated: %s (lap %.1f s in, heading for cp %d)", name, tostring(d.vid), tostring(d.reason), (nowMs() - r.lapStart) / 1000, r.nextCp))
end

function onCross(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or type(d) ~= "table" then log("E", "bad cross payload from " .. pid .. ": " .. tostring(data)); return end

    local r, key = runFor(pid, d.vid)
    r.resets = (r.resets or 0) + (tonumber(d.resets) or 0)      -- physics resets since the last checkpoint, from the client
    local t   = nowMs()
    local who = r.name .. "#" .. tostring(d.vid)
    local n   = #cfg.checkpoints + (cfg.finish and 1 or 0)   -- targets: checkpoints, then the finish line if separate

    dbg(string.format("cross pid=%d vid=%s cp=%s expecting=%d", pid, tostring(d.vid), tostring(d.cp), r.nextCp))

    if d.cp == 1 and d.restart then
        -- v0.26: the start bubble is a waiting area. The car drove into it with no lap to finish: a fresh
        -- car, or one that came back mid-lap (the partial lap is dropped: Darren's rule, 27/10). The clock
        -- sits at zero until a honk countdown, GO and the first car out (onBubble). Nothing starts here.
        atLine(r, pid, d.vid, "partial lap dropped")
        return
    end

    if d.cp ~= r.nextCp then
        -- wrong checkpoint: ignore, but tell the client in debug so a bad radius shows up
        logEvery("wrongcp " .. key, 5000, "I", string.format("cross refused %s: got cp %s, wanted cp %d (of %d)%s", who, tostring(d.cp), r.nextCp, n,
                 r.lapStart and string.format(", lap %.1f s in", (t - r.lapStart) / 1000) or ", no lap running"), t)
        MP.TriggerClientEventJson(pid, "kkLap_info", { vid = d.vid, msg = "wrong cp " .. tostring(d.cp) .. ", want " .. r.nextCp })
        return
    end

    if d.cp == 1 or (cfg.finish and d.cp == n) then
        -- start/finish line (loop), or the separate finish line (point-to-point)
        if r.lapStart then
            local lap = t - r.lapStart
            table.insert(r.sectors, t - r.lastCp)            -- last sector: previous cp -> finish line
            -- a best sector only from a lap that counts: not invalidated, not too short (v0.24;
            -- before, a teleport-shortened lap set a best nobody could beat)
            if not r.invalid and lap >= cfg.minLapMs then
                local idx, sec = #r.sectors, t - r.lastCp
                if not r.bestSectors[idx] or sec < r.bestSectors[idx] then r.bestSectors[idx] = sec end
            end
            local lapDelta = r.best and (lap - r.best) or nil   -- vs your best before this lap (v0.20)
            if lap < cfg.minLapMs then
                log("I", string.format("lap rejected, too short: %s %d ms < minLapMs %d (%d sectors)", who, lap, cfg.minLapMs, #r.sectors))
            elseif r.invalid then
                local name = MP.GetPlayerName(pid) or ("p" .. pid)
                local why  = r.invalidWhy or "position reset"
                log("I", string.format("LAP NOT COUNTED %s %s (invalidated: %s)", who, fmt(lap), why))
                lapRecord(name .. "  NOT COUNTED  " .. fmt(lap) .. "  (" .. why .. ")")
                say(-1, name .. "  " .. fmt(lap) .. "  not counted")
            else
                r.laps = r.laps + 1
                local name = MP.GetPlayerName(pid) or ("p" .. pid)
                local kph  = math.floor(tonumber(d.kph) or 0)
                if not r.best or lap < r.best then
                    -- everything the board shows about the best lap is kept WITH it (v0.24): before, the
                    -- board took the best time but the sectors, speed and lap count of the latest lap
                    r.best = lap; r.bestResets = r.resets; r.bestKph = kph
                    r.bestSectorsOfBest = {}
                    for i, v in ipairs(r.sectors) do r.bestSectorsOfBest[i] = v end
                end
                MP.TriggerClientEventJson(-1, "kkLap_lap", { pid = pid, vid = d.vid, name = name, lapMs = lap, bestMs = r.best,
                                                            laps = r.laps, sectors = r.sectors, kph = kph, resets = r.resets, deltaMs = lapDelta })
                local line = string.format("%s  lap %d  %s  %s  best %s  @%d km/h  resets %d%s",
                                           name, r.laps, fmt(lap), sectorsText(r.sectors), fmt(r.best), kph, r.resets,
                                           r.raceNo and ("  race " .. r.raceNo) or "")
                log("I", "LAP " .. line)
                lapRecord(line)
                if cfg.announceLaps then say(-1, line) end
                -- the board: only once this driver has minLaps laps in this run; then ANY of their laps can be the record
                if r.laps >= cfg.minLaps then
                    local bkey = playerKey(pid)
                    local e = board[bkey]
                    -- laps on the board = counted laps: the first time this run qualifies, all of its laps so far
                    local add = (r.laps == cfg.minLaps) and r.laps or 1
                    local bs = {}
                    for i, v in pairs(r.bestSectors) do bs[i] = v end
                    if not e or r.best < e.bestMs then
                        board[bkey] = { name = name, bestMs = r.best, sectors = r.bestSectorsOfBest or {}, laps = (e and e.laps or 0) + add,
                                        kph = r.bestKph or 0, resets = r.bestResets or 0, bestSectors = bs }
                    else
                        e.laps = (e.laps or 0) + add; e.name = name; e.bestSectors = bs
                    end
                    saveBoard()
                else
                    log("I", string.format("board: %s has %d of %d laps this run, not on the board yet", who, r.laps, cfg.minLaps))
                    if r.laps == 1 then say(pid, string.format("one more lap and your best counts for the board (minimum %d)", cfg.minLaps)) end
                end
            end
            r.resets = 0; r.invalid = false; r.invalidWhy = nil
            if cfg.finish then
                r.lapStart = nil; r.lastCp = nil; r.sectors = {}; r.nextCp = 1   -- course done: back into the start bubble, honk to go again
            else
                startLap(r, t, "drive-through: the next lap starts at once")
            end
        else
            -- the line (or the finish) crossed with no lap running: nothing starts. The clock only ever
            -- starts after a honk countdown (v0.26). This is a stale client (the server reloaded mid-lap)
            -- or a hand-made event; the car is told it is at the line and goes back to the bubble to honk.
            logEvery("nolap " .. key, 5000, "I", string.format("cross %s with no lap running: no timing without a honk countdown", who), t)
            atLine(r, pid, d.vid, "cross with no lap running")
        end
    else
        -- a split: sector = time since the previous checkpoint, split = time since the line
        if not r.lapStart then
            -- a GO starter at another checkpoint before anyone drove out of the bubble (a teleport): no clock to split
            log("I", string.format("split at cp %s from %s skipped: no lap clock yet (%s)", tostring(d.cp), who,
                r.pendingGo and "GO start, nobody has left the bubble" or "no lap running"))
            return
        end
        local sector = t - r.lastCp
        table.insert(r.sectors, sector)
        r.lastCp = t
        local kph = math.floor(tonumber(d.kph) or 0)
        local idx = #r.sectors
        local bs  = r.bestSectors[idx]
        local delta = bs and (sector - bs) or nil            -- vs your own best for this sector (v0.20)
        if not r.invalid and (not bs or sector < bs) then r.bestSectors[idx] = sector end   -- not from an invalidated lap (v0.24)
        MP.TriggerClientEventJson(pid, "kkLap_split", { vid = d.vid, cp = d.cp, splitMs = t - r.lapStart, sectorMs = sector, kph = kph, deltaMs = delta })
        if cfg.announceSplits then
            local name = MP.GetPlayerName(pid) or ("p" .. pid)
            say(-1, string.format("%s  S%d %s s  split %s  @%d km/h", name, d.cp - 1, sec(sector), fmt(t - r.lapStart), kph))
        end
        r.nextCp = (d.cp % n) + 1
    end
end

-- ------------------------------------------------- which client mod version each player runs (v0.25)
-- The client says it on loading ("extension v0.25 loaded", a v0.24 client too), in its alive
-- line, on kkLap_getcfg and on kkLap_ack (sent for every config it receives). A player on an old
-- zip (the game kept a cached copy) is told once and the log says so once.
local clientVer, verWarned = {}, {}
local function noteClientVersion(pid, v)
    if v == nil or v == "" then return end
    v = tostring(v)
    clientVer[pid] = v
    if v ~= VERSION and not verWarned[pid] then
        verWarned[pid] = true
        local name = MP.GetPlayerName(pid) or ("p" .. pid)
        log("W", string.format("client %s runs v%s, server is v%s", name, v, VERSION))
        say(pid, string.format("kkLap: your client mod is v%s, server is v%s, rejoin to re-download", v, VERSION))
    end
end

-- client debug lines land in the server log with the same tag.
-- v0.25: the "alive" line (every 5 s per client, 80% of the log) is logged once a minute per
-- client, or at once when what it says changes. Lines starting "ERROR " are always logged.
local alive = {}          -- pid -> { t, what, n = lines not logged since }
function onClientDbg(pid, data)
    data = tostring(data)
    noteClientVersion(pid, data:match("^extension v(%S+) loaded") or data:match("^alive.*%(v([^%)]+)%)%s*$"))
    local isErr = data:sub(1, 6) == "ERROR "
    if not (cfg.debug or isErr) then return end
    if data:sub(1, 5) == "alive" then
        local what, t = data:gsub("%s*%(v[^%)]*%)%s*$", ""), nowMs()
        local a = alive[pid]
        if a and a.what == what and t - a.t < 60000 then a.n = a.n + 1; return end
        if a and a.n > 0 then data = data .. string.format("  (%d alive lines since the last one, same)", a.n) end
        alive[pid] = { t = t, what = what, n = 0 }
    end
    log("C" .. pid, data)
end

-- client extension just loaded and wants the current config (v0.25: the payload carries its version)
function onGetCfg(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if ok and type(d) == "table" then noteClientVersion(pid, d.v) end
    sendCfg(pid)
end
-- client got a config: { v = its version, rev = trackRev, cps = checkpoints, reset = its lap state was reset }
function onAck(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or type(d) ~= "table" then log("E", "bad ack from " .. pid .. ": " .. tostring(data)); return end
    noteClientVersion(pid, d.v)
end

-- client answers "where is vehicle X" for /lap mark
-- the client answers /lap mark, /lap start and /lap finish here, echoing "what" it was asked for
-- v0.25: every answer goes to that player AND to kkLap.log (so to the console too, where
-- "lap mark <player>" was typed: the console's own reply is gone by the time the car answers)
local posAsked = {}       -- pid -> what the console asked that player's car for
function onPosReply(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or type(d) ~= "table" or not d.pos then log("E", "bad pos reply: " .. tostring(data)); return end
    local pname = MP.GetPlayerName(pid) or ("p" .. pid)
    local by = posAsked[pid] and " (asked from the console)" or ""
    posAsked[pid] = nil
    local function say(p, msg)                                -- shadows the chat say: chat + log
        MP.SendChatMessage(p, "[LAP] " .. msg)
        log("I", "position from " .. pname .. by .. ": " .. msg)
    end
    local what = d.what or "mark"
    if what == "mark" and #cfg.checkpoints == 0 then what = "start" end   -- first mark IS the start
    local cp = { x = d.pos.x, y = d.pos.y, z = d.pos.z, r = d.r or cfg.defaultRadius }
    if what == "finish" and #cfg.checkpoints == 0 then        -- a finish with no start: the client had no cp1 (v0.24)
        say(pid, "mark the start first (/start), then the finish"); return
    end
    if what == "start" or what == "finish" then
        -- lines: only on tarseal (Darren's rule, 27/10), and they need the car's heading to run across the road
        if not d.sealed then
            say(pid, what .. " must be on tarseal. Wheels are on: " .. tostring(d.surface))
            return
        end
        if type(d.heading) == "table" then cp.hx, cp.hy = tonumber(d.heading.x) or 0, tonumber(d.heading.y) or 1 end
        cp.surface = d.surface
    end
    if what == "start" then
        local old = cfg.checkpoints[1]
        if old and not d.r then cp.r = old.r end           -- keep the radius unless a new one was given
        cfg.checkpoints[1] = cp                            -- replaced in place: every other checkpoint stays
        trackChanged(); saveCfg(); sendCfg(-1)
        say(pid, string.format("start line set at %.1f %.1f %.1f r=%.1f (%d checkpoints kept)", cp.x, cp.y, cp.z, cp.r, #cfg.checkpoints - 1))
    elseif what == "finish" then
        cfg.finish = cp
        trackChanged(); saveCfg(); sendCfg(-1)
        say(pid, string.format("finish line set at %.1f %.1f %.1f r=%.1f: point-to-point course, start no longer finishes a lap", cp.x, cp.y, cp.z, cp.r))
    else
        table.insert(cfg.checkpoints, cp)
        trackChanged(); saveCfg(); sendCfg(-1)              -- v0.24: runs reset too (they did not; the clients did)
        say(pid, string.format("checkpoint %d marked at %.1f %.1f %.1f r=%.1f", #cfg.checkpoints, cp.x, cp.y, cp.z, cp.r))
    end
end

-- ---------------------------------------------------------------- commands: chat and server console
-- v0.25 (Darren, 30/10: "i need ONLY admin commands for server side only terminal"): one command
-- body, runCommand, for both. Chat: /lap mark or /mark, answered in chat; admin commands are refused
-- there unless cfg.adminChat is on. Console (Pterodactyl's Console tab, the BeamMP server console):
-- "lap mark Darren", answered as onConsoleInput's return value, which the console prints.
local CONSOLE = -2        -- the console's "player id": a sentinel, never a real player
local function isAdmin(pid)
    if pid == CONSOLE then return true end          -- whoever has the server console owns the server
    if #cfg.admins == 0 then return true end        -- nobody set yet: open (bootstrap)
    local name = MP.GetPlayerName(pid) or ""
    for _, a in ipairs(cfg.admins) do if a == name then return true end end
    return false
end
local ADMIN_ONLY = { mark = true, start = true, finish = true, clear = true, undo = true, reset = true, debug = true,
                     markers = true, announce = true, autostart = true, set = true, line = true, music = true, race = true, admin = true,
                     hud = true, status = true, report = true, adminchat = true }
-- an admin command: the ADMIN_ONLY words, and the admin halves of two player commands
local function isAdminCmd(cmd, args)
    if ADMIN_ONLY[cmd] then return true end
    if cmd == "rules" and (args[3] == "add" or args[3] == "clear") then return true end
    if cmd == "dj" and args[3] then return true end
    return false
end
local PLAYER_HELP = {
    "/rules   - this window     /times - the board     /help - commands",
    "/dj      - where the Discord DJ set is",
    "Honk in the start bubble to start (twice: start without the rest). Everything else is admin only.",
}
local ADMIN_HELP = {      -- chat spelling, shown only while adminChat is on
    "/mark [r] | /start [r] | /finish [r] | /finish same | /undo | /clear | /list",
    "/race [sec] | /race stop | /reset | /autostart on|off | /announce on|off | /markers on|off",
    "/line paint|draw | /music <file> [vol] [once] | /music stop | /dj <text> | /hud on|off",
    "/rules add <text> | /rules clear | /admin add|remove <name> | /admin list | /status | /report",
    "/set minLapMs | defaultRadius | markerAlpha | lineWidth | countdown | minLaps | pointsMinDrivers | musicVolume",
}
local CONSOLE_HELP = {    -- console spelling: "lap" first ("/lap" and "kklap" work too)
    "kkLap v" .. VERSION .. " admin commands, typed here in the server console:",
    "  lap mark <player> [r] | lap start <player> [r] | lap finish <player> [r]   at that player's car",
    "  lap finish same | lap undo | lap clear | lap list | lap reset",
    "  lap race [sec] | lap race stop | lap autostart on|off | lap announce on|off | lap markers on|off",
    "  lap line paint|draw | lap music <file> [vol] [once] | lap music stop | lap dj [text] | lap hud on|off | lap debug on|off",
    "  lap rules | lap rules add <text> | lap rules clear | lap admin add|remove <name> | lap admin list",
    "  lap set minLapMs|defaultRadius|markerAlpha|lineWidth|countdown|minLaps|pointsMinDrivers|musicVolume <n>",
    "  lap times | lap status (what the plugin sees now) | lap report (writes report.txt to upload)",
    "  lap adminchat on|off   admins may type these in game chat too (off = console only)",
}
-- /set <key> <n>: only these keys, only in range (v0.24). /set countdown 1e999 used to be accepted and
-- jammed the race start for good. integer = rounded down. Anything else in config.json: edit the file.
local SET_KEYS = {
    minLapMs         = { 0, 3600000, integer = true },
    defaultRadius    = { 1, 100 },
    markerAlpha      = { 0, 255, integer = true },
    lineWidth        = { 1, 50 },
    countdown        = { 1, 60, integer = true },
    minLaps          = { 1, 100, integer = true },
    pointsMinDrivers = { 1, 100, integer = true },
    musicVolume      = { 0, 10 },
}
-- on|off commands: no argument SHOWS the value (v0.24; before, a bare /markers switched them off)
local function onOff(reply, pfx, args, key, label, push)
    local a = args[3] and args[3]:lower()
    if a == "on" or a == "off" then
        cfg[key] = (a == "on"); saveCfg(); if push then sendCfg(-1) end
        reply(label .. " " .. tostring(cfg[key]))
    else
        reply(label .. " is " .. (cfg[key] and "on" or "off") .. "  (" .. pfx .. args[2] .. " on|off to change)")
    end
end
local function sendRules(pid, open)
    MP.TriggerClientEventJson(pid, "kkLap_rules", { open = open, rules = cfg.rules, player = PLAYER_HELP,
                                                    admin = (cfg.adminChat and isAdmin(pid)) and ADMIN_HELP or nil })
end

-- a player by name, for the console's "lap mark <player>": exact (any case), else the only one
-- whose name starts with it. MP.GetPlayers(): BeamMP server API, { [id] = name } (TLuaEngine.cpp).
local function findPlayer(who)
    local ok, list = pcall(MP.GetPlayers)
    if not ok or type(list) ~= "table" then return nil, "MP.GetPlayers failed: " .. tostring(list) end
    local low, hit, names = who:lower(), {}, {}
    for id, name in pairs(list) do
        names[#names + 1] = tostring(name)
        if tostring(name):lower() == low then return tonumber(id), tostring(name) end
        if tostring(name):lower():sub(1, #low) == low then hit[#hit + 1] = { tonumber(id), tostring(name) } end
    end
    if #hit == 1 then return hit[1][1], hit[1][2] end
    table.sort(names)
    return nil, (#hit > 1 and "more than one player starts with " .. who or "no player called " .. who) ..
                " (on now: " .. (#names > 0 and table.concat(names, ", ") or "nobody") .. ")"
end

-- ------------------------------------------------- lap status / lap report (v0.25)
local REPORT_PATH = "Resources/Server/kkLap/report.txt"
local function ago(t) return t and string.format("%.1f s ago", (nowMs() - t) / 1000) or "never" end
local function statusLines()
    local L = {}
    local okc, count = pcall(MP.GetPlayerCount)
    local vers = {}
    for pid, v in pairs(clientVer) do vers[#vers + 1] = (MP.GetPlayerName(pid) or ("p" .. pid)) .. " v" .. v end
    table.sort(vers)
    L[#L + 1] = string.format("kkLap v%s, players %s, client mods: %s", VERSION, okc and tostring(count) or "?", #vers > 0 and table.concat(vers, ", ") or "none reported")
    L[#L + 1] = string.format("track: rev %s, %d checkpoints, %s", tostring(cfg.trackRev), #cfg.checkpoints, cfg.finish and "separate finish (point-to-point)" or "loop")
    L[#L + 1] = string.format("race: active %s, %s, GO %s, race no %d", tostring(race.active), race.active and (race.left .. " s left") or "no countdown",
                              ago(race.tGo), race.no)
    local b = bubbleList()
    local parts = {}
    for _, e in ipairs(b) do
        parts[#parts + 1] = tag(e) .. (e.started and (" started (GO " .. tostring(e.goNo) .. ")") or (e.ready and " ready" or " waiting"))
    end
    L[#L + 1] = string.format("bubble: %d  %s", #b, #parts > 0 and ("[" .. table.concat(parts, ", ") .. "]") or "(empty)")
    local keys = {}
    for k in pairs(runs) do keys[#keys + 1] = k end
    table.sort(keys)
    if #keys == 0 then L[#L + 1] = "runs: none" end
    for _, k in ipairs(keys) do
        local r = runs[k]
        local pid, vid = k:match("^(-?%d+):(.*)$")
        L[#L + 1] = string.format("run %s#%s: next cp %d (last cp %s), lap start %s, laps %d, best %s, invalid %s%s%s",
            MP.GetPlayerName(tonumber(pid)) or ("p" .. tostring(pid)), tostring(vid), r.nextCp, r.lapStart and tostring(r.nextCp == 1 and "the last one" or r.nextCp - 1) or "-",
            ago(r.lapStart), r.laps or 0, r.best and fmt(r.best) or "-", tostring(r.invalid == true),
            r.invalidWhy and (" (" .. r.invalidWhy .. ")") or "", (r.raceNo and (", race " .. r.raceNo) or "") .. (r.pendingGo and ", GO start: waiting for the first car out" or ""))
    end
    L[#L + 1] = string.format("cfg: countdown %s, minLaps %s, minLapMs %s, autoStart %s, musicAuto %s, hud %s, adminChat %s, debug %s, trackRev %s, admins %s",
        tostring(cfg.countdown), tostring(cfg.minLaps), tostring(cfg.minLapMs), tostring(cfg.autoStart), tostring(cfg.musicAuto), tostring(cfg.hud),
        tostring(cfg.adminChat == true), tostring(cfg.debug), tostring(cfg.trackRev), #cfg.admins > 0 and table.concat(cfg.admins, ", ") or "none (everyone)")
    return L
end
local function logTail(n)               -- the last n lines of kkLap.log, read from its end
    local f = io.open(LOG_PATH, "r")
    if not f then return {} end
    local size = f:seek("end")
    f:seek("set", math.max(0, size - 400000))
    local txt = f:read("*a") or ""; f:close()
    local lines = {}
    for l in txt:gmatch("[^\n]+") do lines[#lines + 1] = l end
    if size > 400000 then table.remove(lines, 1) end        -- the first one is cut in half
    local out = {}
    for i = math.max(1, #lines - n + 1), #lines do out[#out + 1] = lines[i] end
    return out
end
local function writeReport()
    local f, err = io.open(REPORT_PATH, "w")
    if not f then return nil, err end
    local function json(t)
        local enc = Util.JsonEncode(t)
        local ok, pretty = pcall(Util.JsonPrettify, enc)    -- BeamMP server API (LuaAPI.cpp); plain JSON if missing
        return (ok and type(pretty) == "string") and pretty or enc
    end
    local tail = logTail(300)
    f:write("kkLap report ", os.date("%Y-%m-%d %H:%M:%S"), "  (lap report in the server console)\n\n")
    f:write("== status\n", table.concat(statusLines(), "\n"), "\n\n")
    f:write("== config (as running)\n", json(cfg), "\n\n")
    f:write("== board (times.json)\n")
    for i, it in ipairs(sortedBoard()) do
        f:write(string.format("%d. %s  %s  %s  laps %d  @%d km/h  resets %d\n", i, it.name, fmt(it.e.bestMs), sectorsText(it.e.sectors or {}),
                              it.e.laps or 0, it.e.kph or 0, it.e.resets or 0))
    end
    f:write(json(board), "\n\n")
    f:write("== last ", #tail, " lines of kkLap.log\n", table.concat(tail, "\n"), "\n")
    f:close()
    return REPORT_PATH, #tail
end

-- the words that work on their own, "/mark" = "/lap mark"
SHORT = { help = true, mark = true, start = true, finish = true, list = true, clear = true, undo = true,
          reset = true, debug = true, markers = true, announce = true, autostart = true, race = true,
          times = true, line = true, music = true, set = true, lap = true, dj = true, rules = true, admin = true, hud = true,
          status = true, report = true, adminchat = true }

-- One command, from chat (pid = the player) or the console (pid = CONSOLE). reply(msg) answers
-- whoever typed it; name is who did it, for the broadcast lines. Admin gating is the caller's job.
local function runCommand(pid, args, reply, name)
    local cmd = args[2] or "help"
    local fromConsole = pid == CONSOLE
    local pfx = fromConsole and "lap " or "/"
    -- a line everyone sees in chat; the console sees it too
    local function all(msg) say(-1, msg); if fromConsole then reply(msg) end end
    -- /mark, /start, /finish need a car: the typer's, or from the console the named player's
    local function askPos(what)
        local target, r = pid, tonumber(args[3])
        posAsked[pid] = nil                 -- a player's own /mark: not the console's any more
        if fromConsole then
            local who = args[3]
            if not who then reply("needs a player name: lap " .. what .. " <name> [radius]  (that player's car is the spot)"); return end
            local id, pname = findPlayer(who)
            if not id then reply(pname); return end
            target, r = id, tonumber(args[4])
            posAsked[id] = what
            reply(string.format("asked %s (id %d) where their car is for the %s; the answer goes to them and to kkLap.log (this console)", pname, id, what))
        end
        MP.TriggerClientEventJson(target, "kkLap_getpos", { r = r, what = what })
    end

    if cmd == "help" then
        if fromConsole then for _, l in ipairs(CONSOLE_HELP) do reply(l) end
            reply("adminchat is " .. (cfg.adminChat and "on: admins may also type the /commands in game chat" or "off: admin commands work here only"))
            if #cfg.admins == 0 and cfg.adminChat then reply("NO ADMINS SET: with adminchat on, everyone can change things in chat. lap admin add <name> locks it.") end
        else
            for _, l in ipairs(PLAYER_HELP) do reply(l) end
            if cfg.adminChat and isAdmin(pid) then
                for _, l in ipairs(ADMIN_HELP) do reply(l) end
                if #cfg.admins == 0 then reply("NO ADMINS SET: everyone can change things. /admin add <name> locks it.") end
            else
                reply("admins: use the server console (type lap help there)")
            end
        end
    elseif cmd == "mark" then
        askPos("mark")                      -- reply comes to onPosReply
    elseif cmd == "start" then
        askPos("start")
    elseif cmd == "finish" and args[3] == "same" then
        cfg.finish = nil; trackChanged(); saveCfg(); sendCfg(-1); reply("finish line removed: start line is the finish again (loop)")
    elseif cmd == "finish" and #cfg.checkpoints == 0 then
        reply("mark the start first (" .. pfx .. "start), then the finish")   -- v0.24: a finish alone left the client without a cp1
    elseif cmd == "finish" then
        askPos("finish")
    elseif cmd == "list" then
        if #cfg.checkpoints == 0 then reply("no checkpoints") end
        if cfg.finish then reply(string.format("finish: %.1f %.1f %.1f r=%.1f (point-to-point)", cfg.finish.x, cfg.finish.y, cfg.finish.z, cfg.finish.r)) end
        for i, c in ipairs(cfg.checkpoints) do
            reply(string.format("%d: %.1f %.1f %.1f r=%.1f", i, c.x, c.y, c.z, c.r))
        end
    elseif cmd == "clear" then
        cfg.checkpoints = {}; cfg.finish = nil; trackChanged(); board = {}; saveCfg(); saveBoard(); sendCfg(-1); reply("checkpoints and finish cleared, best times wiped (laps.log keeps every lap)")
    elseif cmd == "undo" then
        table.remove(cfg.checkpoints)
        local also = ""
        if #cfg.checkpoints == 0 and cfg.finish then cfg.finish = nil; also = ", finish line removed too (no start left)" end   -- v0.24
        trackChanged(); saveCfg(); sendCfg(-1); reply("last checkpoint removed, " .. #cfg.checkpoints .. " left" .. also)
    elseif cmd == "reset" then
        trackChanged(); saveCfg(); sendCfg(-1); reply("all timing reset")   -- the new trackRev clears every client's state
    elseif cmd == "race" and args[3] == "stop" then
        -- v0.24: a countdown can be called off. Clients get a cancel (their "counting" goes off,
        -- else every honk is refused), and the waiting cars must honk again.
        if race.active then
            race.active = false; MP.CancelEventTimer("kkLap_tick")
            for _, e in pairs(bubble) do if not e.started then e.ready = false end end
            MP.TriggerClientEventJson(-1, "kkLap_count", { cancel = true })
            all("countdown cancelled"); log("I", "countdown cancelled by " .. name)
        else
            reply("no countdown running")
        end
    elseif cmd == "race" then
        local sec = tonumber(args[3])
        if sec and sec == sec then cfg.countdown = math.floor(math.max(1, math.min(60, sec))) end   -- 1..60 (v0.24)
        local ok, why = startCountdown(name)
        if ok then if fromConsole then reply("countdown started, " .. race.left .. " s") end
        else reply("no countdown: " .. why); log("I", "race by " .. name .. ": no countdown: " .. why) end
    elseif cmd == "autostart" then
        onOff(reply, pfx, args, "autoStart", "autostart", false)
    elseif cmd == "line" and (args[3] == "paint" or args[3] == "draw") then
        cfg.lineStyle = args[3]; saveCfg(); sendCfg(-1); reply("start line style " .. args[3])
    elseif cmd == "rules" and args[3] == "add" and args[4] then
        table.insert(cfg.rules, table.concat(args, " ", 4)); saveCfg(); reply("rule added (" .. #cfg.rules .. ")")
    elseif cmd == "rules" and args[3] == "clear" then
        cfg.rules = {}; saveCfg(); reply("rules cleared")
    elseif cmd == "rules" and fromConsole then
        if #cfg.rules == 0 then reply("no rules (lap rules add <text>)") end
        for i, l in ipairs(cfg.rules) do reply(i .. ". " .. l) end
    elseif cmd == "rules" then
        sendRules(pid, nil)                  -- nil = toggle on the client
    elseif cmd == "admin" and args[3] == "add" and args[4] then
        table.insert(cfg.admins, args[4]); saveCfg(); all(args[4] .. " is now a kkLap admin (by " .. name .. ")")
    elseif cmd == "admin" and args[3] == "remove" and args[4] then
        for i = #cfg.admins, 1, -1 do if cfg.admins[i] == args[4] then table.remove(cfg.admins, i) end end
        saveCfg(); reply("admins: " .. table.concat(cfg.admins, ", "))
    elseif cmd == "admin" then
        reply("admins: " .. (#cfg.admins > 0 and table.concat(cfg.admins, ", ") or "none set (everyone)"))
    elseif cmd == "adminchat" then
        local a = args[3] and args[3]:lower()
        if not fromConsole then
            reply("adminchat is set from the server console only: lap adminchat on|off")
        elseif a == "on" or a == "off" then
            cfg.adminChat = (a == "on"); saveCfg()
            reply("adminchat " .. a .. ": admin commands typed in game chat are " .. (cfg.adminChat and "allowed for admins (" .. (#cfg.admins > 0 and table.concat(cfg.admins, ", ") or "nobody set: EVERYONE") .. ")" or "refused, console only"))
        else
            reply("adminchat is " .. (cfg.adminChat and "on" or "off") .. "  (lap adminchat on|off to change)")
        end
    elseif cmd == "dj" and args[3] then
        cfg.djText = table.concat(args, " ", 3); saveCfg(); all("[DJ] " .. cfg.djText)
    elseif cmd == "dj" then
        all("[DJ] " .. tostring(cfg.djText))
    elseif cmd == "music" and args[3] == "stop" then
        cfg.musicAuto = nil; cfg.musicSeconds = nil; saveCfg(); sendCfg(-1)      -- clears the autoplay for joiners too
        MP.TriggerClientEventJson(-1, "kkLap_music", { stop = true }); all("music stopped by " .. name)
    elseif cmd == "music" and args[3] then
        -- /music <name> [volume 0-1] [once | <seconds>]   loops by default (DJ set); "once" plays through;
        -- a number of seconds is the fallback loop clock for games that give us no stoppable source
        -- volume: 0-1 is normal, above 1 is a boost (RLS passes volume=2 to the same call).
        -- Capped at 10 so a typo does not deafen the server. YouTube rips are quiet (Darren, 27/10).
        local vol  = math.min(10, math.max(0, tonumber(args[4]) or cfg.musicVolume))
        local loop = args[5] ~= "once"
        if loop then
            -- the config carries it (autoplay for joiners too) and is the ONLY play message (v0.24: the
            -- kkLap_music sent as well made every client play it twice, and leak the first source)
            cfg.musicAuto = args[3]; cfg.musicVolume = vol; cfg.musicSeconds = tonumber(args[5])
            cfg.musicNo = math.floor(tonumber(cfg.musicNo) or 0) + 1   -- a new /music restarts the track even if it is the same one
            saveCfg(); sendCfg(-1)
        else
            MP.TriggerClientEventJson(-1, "kkLap_music", { file = args[3], vol = vol, loop = false })
        end
        all(string.format("music: %s (%s, volume %.0f%%%s)", args[3], name, vol * 100, loop and ", on repeat" or ""))
        log("I", "music " .. args[3] .. " by " .. name)
    elseif cmd == "times" then
        local list = sortedBoard()
        if #list == 0 then reply(string.format("no times on the board yet (a driver needs %d laps first)", cfg.minLaps)) end
        local showPoints = #list >= cfg.pointsMinDrivers
        if #list > 0 and not showPoints then reply(string.format("points appear once %d drivers have times (%d so far)", cfg.pointsMinDrivers, #list)) end
        for i, it in ipairs(list) do
            if i > 10 then break end
            local pts = showPoints and string.format("  %d pts", pointsFor(it.e.bestMs, list[1].e.bestMs)) or ""
            reply(string.format("%d. %s  %s  %s  (%d laps, @%d km/h, resets %d)%s", i, it.name, fmt(it.e.bestMs),
                                sectorsText(it.e.sectors or {}), it.e.laps or 0, it.e.kph or 0, it.e.resets or 0, pts))
        end
    elseif cmd == "announce" then
        onOff(reply, pfx, args, "announceSplits", "sector announcements to everyone", false)
    elseif cmd == "markers" then
        onOff(reply, pfx, args, "markers", "markers", true)
    elseif cmd == "hud" then
        onOff(reply, pfx, args, "hud", "on-screen HUD", true)
    elseif cmd == "debug" then
        onOff(reply, pfx, args, "debug", "debug", true)
    elseif cmd == "status" then
        for _, l in ipairs(statusLines()) do reply(l) end
    elseif cmd == "report" then
        local path, n = writeReport()
        if path then reply(string.format("report written: %s (status, config, board, last %d lines of kkLap.log). Download it from the panel's Files tab and upload it.", path, n))
        else reply("could not write the report: " .. tostring(n)); log("E", "report not written: " .. tostring(n)) end
    elseif cmd == "set" and args[3] and tonumber(args[4]) then
        local rule, v = SET_KEYS[args[3]], tonumber(args[4])
        if not rule then
            reply("unknown numeric option " .. args[3])
        else
            if rule.integer and v == v then v = math.floor(v) end     -- v == v: not NaN
            if not (v == v and v >= rule[1] and v <= rule[2]) then
                reply(string.format("%s must be %s..%s%s, not %s", args[3], tostring(rule[1]), tostring(rule[2]),
                                    rule.integer and " (whole number)" or "", args[4]))
            else
                cfg[args[3]] = v; saveCfg(); sendCfg(-1); reply(args[3] .. " = " .. tostring(v))
            end
        end
    else
        reply(fromConsole and ("unknown kkLap command '" .. tostring(cmd) .. "', try: lap help") or "unknown /lap command, try /lap help")
    end
end

function onChatMessage(pid, name, msg)
    -- v0.12: "/mark" works as well as "/lap mark". Any other "/word" is left alone for
    -- other plugins. Unknown words after "/lap" still get the help text.
    if msg:sub(1, 1) ~= "/" then return end
    local args = {}
    for w in msg:gmatch("%S+") do table.insert(args, w) end
    if args[1] ~= "/lap" then
        local short = args[1]:sub(2):lower()
        if not SHORT[short] then return end           -- not ours
        table.insert(args, 1, "/lap")                 -- "/mark 10" -> "/lap mark 10"
        args[2] = short
    end
    local cmd = args[2] or "help"
    local function reply(m) say(pid, m) end
    if isAdminCmd(cmd, args) then
        if cmd == "adminchat" then reply("adminchat is set from the server console only: lap adminchat on|off"); return 1 end
        if not cfg.adminChat then
            -- v0.25: admins included. Logged, so "it did nothing" has an answer in kkLap.log
            reply("admin commands run from the server console only")
            log("I", string.format("chat command refused (adminChat off, console only): %s: %s", tostring(name), msg))
            return 1
        end
        if not isAdmin(pid) then
            if cmd == "rules" then reply("admin only: /rules add and /rules clear. /rules on its own opens the window.")   -- v0.24
            elseif cmd == "dj" then reply("admin only")
            else reply("admin only. Admins: " .. table.concat(cfg.admins, ", ")) end
            log("I", string.format("chat command refused (not an admin): %s: %s", tostring(name), msg))
            return 1
        end
    end
    runCommand(pid, args, reply, name)
    return 1
end

-- v0.25: the server console. BeamMP (TConsole.cpp RunAsCommand) fires onConsoleInput with every
-- line typed there; a returned string is printed, nil means "not mine" (BeamMP then says
-- "Unknown command" unless another plugin answered, and its own words like status and help still run).
function onConsoleInput(line)
    if type(line) ~= "string" then return nil end
    local args = {}
    for w in line:gmatch("%S+") do table.insert(args, w) end
    local head = args[1] and args[1]:lower()
    if head ~= "lap" and head ~= "/lap" and head ~= "kklap" then return nil end   -- not ours
    args[1] = "/lap"
    if args[2] then args[2] = args[2]:lower() end
    log("CONSOLE", line)                                    -- every console command leaves a record
    local out = {}
    local ok, err = pcall(runCommand, CONSOLE, args, function(m) out[#out + 1] = tostring(m) end, "console")
    if not ok then
        log("E", "console command failed: " .. line .. ": " .. tostring(err))
        out[#out + 1] = "kkLap: that command failed (" .. tostring(err) .. "), it is in kkLap.log"
    end
    if #out == 0 then out[1] = "ok" end
    return table.concat(out, "\n")
end

-- ---------------------------------------------------------------- lifecycle
function onPlayerJoin(pid)
    sendCfg(pid)                          -- late joiner gets the current checkpoints
    if cfg.rulesOnJoin then sendRules(pid, true) end
end
function onPlayerDisconnect(pid)
    for k, e in pairs(bubble) do
        if k:match("^" .. pid .. ":") then bubble[k] = nil; log("I", string.format("bubble -%s (now %d, player left)", tag(e), bubbleSize())) end
    end
    clientVer[pid], verWarned[pid], alive[pid], posAsked[pid] = nil, nil, nil, nil
    for k in pairs(runs) do
        if k:match("^" .. pid .. ":") then runs[k] = nil end
    end
    bubbleLeft()                          -- the leaver may have been the one nobody could start without
end

function onInit()
    local fromFile = loadCfg()
    loadBoard()
    MP.RegisterEvent("onChatMessage",      "onChatMessage")
    MP.RegisterEvent("onPlayerJoin",       "onPlayerJoin")
    MP.RegisterEvent("onPlayerDisconnect", "onPlayerDisconnect")
    MP.RegisterEvent("kkLap_cross",        "onCross")
    MP.RegisterEvent("kkLap_dbg",          "onClientDbg")
    MP.RegisterEvent("kkLap_posreply",     "onPosReply")
    MP.RegisterEvent("kkLap_getcfg",       "onGetCfg")
    MP.RegisterEvent("kkLap_arm",          "onArm")
    MP.RegisterEvent("kkLap_bubble",       "onBubble")
    MP.RegisterEvent("kkLap_invalid",      "onInvalid")
    MP.RegisterEvent("kkLap_tick",         "onTick")
    MP.RegisterEvent("kkLap_ack",          "onAck")            -- v0.25: client got the config, says its version
    MP.RegisterEvent("onConsoleInput",     "onConsoleInput")   -- v0.25: "lap ..." typed in the server console
    log("I", PLUGIN .. " v" .. VERSION .. " loaded on " .. MP.GetOSName())
    -- a (re)load of this file starts with no runs and no bubble, so it is a track change for the
    -- clients too, and they must hear it: on a hot reload nobody re-joins, so nobody would ask for
    -- the config, and a client caught mid-countdown kept "counting" (every honk refused) for good (v0.24).
    -- MP.GetPlayerCount: BeamMP server API (TLuaEngine.cpp). No players = nobody to tell.
    trackChanged()
    if fromFile then saveCfg() end        -- so the next start counts on from here; no file = no track yet, nothing written
    local okc, count = pcall(MP.GetPlayerCount)
    if okc and tonumber(count) and tonumber(count) > 0 then sendCfg(-1) end
end
