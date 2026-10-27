-- kkLap : server plugin (BeamMP server Lua, v3.x API)
--
-- The server is the switchboard. It never looks at the world. It:
--   * holds the checkpoint list and the options (config.json beside this file)
--   * receives "I crossed checkpoint N" from clients and does the timing
--   * broadcasts results and config changes to every client
--   * answers /lap chat commands so options can be changed live, in game
--
-- Every line this plugin prints starts with [KKLAP] so one grep finds it:
--     grep KKLAP Server.log
-- Client-side debug lines are forwarded here too (kkLap_dbg), so the server log
-- is the single place to read when something goes wrong.

local PLUGIN   = "kkLap"
local VERSION  = "0.14"   -- same number as the client zip it was built with
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
    musicVolume  = 0.5,      -- /lap music <name> plays art/sound/kkLap/<name>.ogg from the zip on every client (v0.11)
    announceLaps = true,     -- every lap goes to everyone in chat (v0.5.0)
    announceSplits = false,  -- every sector goes to everyone in chat too: noisy, off by default
    checkpoints  = {},
    finish       = nil,      -- a separate finish line {x,y,z,r,hx,hy} = point-to-point; nil = finish is the start (loop)
}
cfg = nil   -- global on purpose: other kkLap files (if any) can see it

local function loadCfg()
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
local function sendCfg(pid)
    -- a direct send carries the receiver's own player id, so the client knows which laps are its own
    cfg.yourPid = (pid ~= -1) and pid or nil
    MP.TriggerClientEventJson(pid, "kkLap_cfg", cfg)
    cfg.yourPid = nil
    dbg("cfg sent to " .. pid)
end

-- ---------------------------------------------------------------- timing state
-- key = pid .. ":" .. vid   (split-screen = one pid, two vids, so vid is essential)
local runs  = {}
local timer = MP.CreateTimer()          -- server clock, seconds as float
local function nowMs() return math.floor(timer:GetCurrent() * 1000 + 0.5) end   -- round, not floor: 59.3 s * 1000 is 59299.999 in floating point (bench, 27/10)

local function fmt(ms)
    local m = math.floor(ms / 60000)
    local s = (ms % 60000) / 1000
    return string.format("%d:%06.3f", m, s)
end

local function say(pid, msg) MP.SendChatMessage(pid, "[LAP] " .. msg) end

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
local function sortedBoard()
    local list = {}
    for name, e in pairs(board) do table.insert(list, { name = name, e = e }) end
    table.sort(list, function(a, b) return a.e.bestMs < b.e.bestMs end)
    return list
end

-- ------------------------------------------------- race start: shared countdown, one GO instant for all
local race = { active = false, left = 0, tGo = nil }
local function startCountdown(who)
    if race.active then return end
    if race.tGo and nowMs() - race.tGo < 10000 then return end       -- just started one, ignore the parked cars
    if #cfg.checkpoints == 0 then return end
    race.active = true
    race.left   = math.max(1, math.floor(cfg.countdown))
    say(-1, string.format("race start in %d  (%s)  - sit in the start bubble", race.left, who))
    MP.TriggerClientEventJson(-1, "kkLap_count", { n = race.left })
    MP.CreateEventTimer("kkLap_tick", 1000)
    log("I", "countdown started by " .. who)
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
        race.tGo    = nowMs()                 -- the one instant every GO-starter's lap counts from
        say(-1, "GO!")
        MP.TriggerClientEventJson(-1, "kkLap_go", { t = race.tGo })
        log("I", "GO at " .. race.tGo)
    end
end
function onArm(pid, data)                      -- client: "my car honked inside the start bubble"
    if not cfg.autoStart then return end
    startCountdown(MP.GetPlayerName(pid) or ("p" .. pid))
end
function onJump(pid, data)                     -- client: "my car left the bubble during the countdown"
    local name = MP.GetPlayerName(pid) or ("p" .. pid)
    say(-1, name .. " jumped the start")
    log("I", name .. " jumped the start")
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
    if not r or not r.lapStart then return end
    r.invalid = true
    local name = MP.GetPlayerName(pid) or ("p" .. pid)
    say(-1, name .. "  lap will not count: " .. tostring(d.reason))
    log("I", name .. " lap invalidated: " .. tostring(d.reason))
end

function onCross(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or type(d) ~= "table" then log("E", "bad cross payload from " .. pid .. ": " .. tostring(data)); return end

    local key = pid .. ":" .. tostring(d.vid)
    local r   = runs[key] or { nextCp = 1, lapStart = nil, lastCp = nil, best = nil, laps = 0, sectors = {}, resets = 0, invalid = false }
    r.resets = (r.resets or 0) + (tonumber(d.resets) or 0)      -- physics resets since the last checkpoint, from the client
    runs[key] = r
    local t   = nowMs()
    local n   = #cfg.checkpoints + (cfg.finish and 1 or 0)   -- targets: checkpoints, then the finish line if separate

    dbg(string.format("cross pid=%d vid=%s cp=%s expecting=%d", pid, tostring(d.vid), tostring(d.cp), r.nextCp))

    if d.cp == 1 and d.go then
        -- GO start (before the wrong-cp check: a car that rolled into the bubble expects cp2 already): the car was sitting in the bubble at GO. Its lap counts from the GO instant,
        -- the same instant for everyone, not from when this packet arrived.
        if race.tGo and t - race.tGo < 5000 then
            r.lapStart = race.tGo; r.lastCp = race.tGo; r.sectors = {}; r.resets = 0; r.invalid = false
            r.nextCp   = (n > 1) and 2 or 1
            MP.TriggerClientEventJson(pid, "kkLap_info", { vid = d.vid, msg = "GO start" })
        end
        return
    end

    if d.cp ~= r.nextCp then
        -- wrong checkpoint: ignore, but tell the client in debug so a bad radius shows up
        MP.TriggerClientEventJson(pid, "kkLap_info", { vid = d.vid, msg = "wrong cp " .. tostring(d.cp) .. ", want " .. r.nextCp })
        return
    end

    if cfg.finish and d.cp == 1 then
        -- point-to-point: the start only starts. Crossing it mid-run restarts the run.
        if r.lapStart then dbg("run restarted at the start line") end
        MP.TriggerClientEventJson(pid, "kkLap_info", { vid = d.vid, msg = "timing started" })
        r.lapStart = t; r.lastCp = t; r.sectors = {}; r.resets = 0; r.invalid = false
        r.nextCp   = (n > 1) and 2 or 1
        return
    end

    if d.cp == 1 or (cfg.finish and d.cp == n) then
        -- start/finish line (loop), or the separate finish line (point-to-point)
        if r.lapStart then
            local lap = t - r.lapStart
            table.insert(r.sectors, t - r.lastCp)            -- last sector: previous cp -> finish line
            if lap < cfg.minLapMs then
                dbg("lap rejected, too short: " .. lap)
            elseif r.invalid then
                local name = MP.GetPlayerName(pid) or ("p" .. pid)
                log("I", "LAP NOT COUNTED " .. name .. " " .. fmt(lap) .. " (invalidated)")
                lapRecord(name .. "  NOT COUNTED  " .. fmt(lap) .. "  (position reset)")
                say(-1, name .. "  " .. fmt(lap) .. "  not counted")
            else
                r.laps = r.laps + 1
                if not r.best or lap < r.best then r.best = lap end
                local name = MP.GetPlayerName(pid) or ("p" .. pid)
                local kph  = math.floor(tonumber(d.kph) or 0)
                if lap == r.best then r.bestResets = r.resets end
                MP.TriggerClientEventJson(-1, "kkLap_lap", { pid = pid, vid = d.vid, name = name, lapMs = lap, bestMs = r.best,
                                                            laps = r.laps, sectors = r.sectors, kph = kph, resets = r.resets })
                local line = string.format("%s  lap %d  %s  %s  best %s  @%d km/h  resets %d",
                                           name, r.laps, fmt(lap), sectorsText(r.sectors), fmt(r.best), kph, r.resets)
                log("I", "LAP " .. line)
                lapRecord(line)
                if cfg.announceLaps then say(-1, line) end
                -- the board: only once this driver has minLaps laps in this run; then ANY of their laps can be the record
                if r.laps >= cfg.minLaps then
                    local e = board[name]
                    if not e or r.best < e.bestMs then
                        board[name] = { bestMs = r.best, sectors = r.sectors, laps = (e and e.laps or 0) + 1, kph = kph, resets = r.bestResets or 0 }
                    else
                        e.laps = e.laps + 1
                    end
                    saveBoard()
                elseif r.laps == 1 then
                    say(pid, string.format("one more lap and your best counts for the board (minimum %d)", cfg.minLaps))
                end
            end
        else
            MP.TriggerClientEventJson(pid, "kkLap_info", { vid = d.vid, msg = "timing started" })
        end
        r.resets = 0; r.invalid = false
        if cfg.finish then
            r.lapStart = nil; r.lastCp = nil; r.sectors = {}; r.nextCp = 1   -- course done: back to the start to go again
        else
            r.lapStart = t
            r.lastCp   = t
            r.sectors  = {}
            r.nextCp   = (n > 1) and 2 or 1
        end
    else
        -- a split: sector = time since the previous checkpoint, split = time since the line
        local sector = t - r.lastCp
        table.insert(r.sectors, sector)
        r.lastCp = t
        local kph = math.floor(tonumber(d.kph) or 0)
        MP.TriggerClientEventJson(pid, "kkLap_split", { vid = d.vid, cp = d.cp, splitMs = t - r.lapStart, sectorMs = sector, kph = kph })
        if cfg.announceSplits then
            local name = MP.GetPlayerName(pid) or ("p" .. pid)
            say(-1, string.format("%s  S%d %s s  split %s  @%d km/h", name, d.cp - 1, sec(sector), fmt(t - r.lapStart), kph))
        end
        r.nextCp = (d.cp % n) + 1
    end
end

-- client debug lines land in the server log with the same tag
function onClientDbg(pid, data)
    if cfg.debug then log("C" .. pid, data) end
end

-- client extension just loaded and wants the current config
function onGetCfg(pid, data)
    sendCfg(pid)
end

-- client answers "where is vehicle X" for /lap mark
-- the client answers /lap mark, /lap start and /lap finish here, echoing "what" it was asked for
function onPosReply(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or type(d) ~= "table" or not d.pos then log("E", "bad pos reply: " .. tostring(data)); return end
    local what = d.what or "mark"
    if what == "mark" and #cfg.checkpoints == 0 then what = "start" end   -- first mark IS the start
    local cp = { x = d.pos.x, y = d.pos.y, z = d.pos.z, r = d.r or cfg.defaultRadius }
    if what == "start" or what == "finish" then
        -- lines: only on tarseal (Darren's rule, 27/10), and they need the car's heading to run across the road
        if not d.sealed then
            say(pid, what .. " must be on tarseal. Wheels are on: " .. tostring(d.surface))
            log("I", what .. " mark refused, surface " .. tostring(d.surface))
            return
        end
        if type(d.heading) == "table" then cp.hx, cp.hy = tonumber(d.heading.x) or 0, tonumber(d.heading.y) or 1 end
        cp.surface = d.surface
    end
    if what == "start" then
        local old = cfg.checkpoints[1]
        if old and not d.r then cp.r = old.r end           -- keep the radius unless a new one was given
        cfg.checkpoints[1] = cp                            -- replaced in place: every other checkpoint stays
        runs = {}; saveCfg(); sendCfg(-1)
        say(pid, string.format("start line set at %.1f %.1f %.1f r=%.1f (%d checkpoints kept)", cp.x, cp.y, cp.z, cp.r, #cfg.checkpoints - 1))
    elseif what == "finish" then
        cfg.finish = cp
        runs = {}; saveCfg(); sendCfg(-1)
        say(pid, string.format("finish line set at %.1f %.1f %.1f r=%.1f: point-to-point course, start no longer finishes a lap", cp.x, cp.y, cp.z, cp.r))
    else
        table.insert(cfg.checkpoints, cp)
        saveCfg(); sendCfg(-1)
        say(pid, string.format("checkpoint %d marked at %.1f %.1f %.1f r=%.1f", #cfg.checkpoints, cp.x, cp.y, cp.z, cp.r))
    end
end

-- ---------------------------------------------------------------- chat commands
-- Return 1 swallows the message so it doesn't show in chat.
-- the words that work on their own, "/mark" = "/lap mark"
SHORT = { help = true, mark = true, start = true, finish = true, list = true, clear = true, undo = true,
          reset = true, debug = true, markers = true, announce = true, autostart = true, race = true,
          times = true, line = true, music = true, set = true, lap = true, dj = true }

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

    if cmd == "help" then
        say(pid, "every command works without the lap word too: /mark, /start, /times, /race ...")
        say(pid, "/lap mark [radius]  - checkpoint at your car (1st = start/finish)")
        say(pid, "/lap start [radius]  - move the start line here, checkpoints kept")
        say(pid, "/lap finish [radius] | finish same  - separate finish line here (point-to-point), or back to a loop")
        say(pid, "/lap times  - best lap per player, with sectors")
        say(pid, "/lap music <name> [0-1] | music stop  - play art/sound/kkLap/<name>.ogg for everyone")
        say(pid, "/dj [new text]  - tell everyone where the Discord DJ set is")
        say(pid, "/lap line paint|draw | set lineWidth <m>  - the start/finish line look")
        say(pid, "/lap race [seconds]  - countdown for everyone sitting in the start bubble (or honk in the bubble)")
        say(pid, "/lap autostart on|off | set countdown <s>")
        say(pid, "/lap list | clear | undo | reset | debug on|off | markers on|off | announce on|off")
        say(pid, "/lap set minLapMs <n> | set defaultRadius <n> | set markerAlpha 0-255 | set minLaps 2 | set pointsMinDrivers 3")
    elseif cmd == "mark" then
        -- ask the client for its current vehicle position; reply comes to onPosReply
        MP.TriggerClientEventJson(pid, "kkLap_getpos", { r = tonumber(args[3]), what = "mark" })
    elseif cmd == "start" then
        MP.TriggerClientEventJson(pid, "kkLap_getpos", { r = tonumber(args[3]), what = "start" })
    elseif cmd == "finish" and args[3] == "same" then
        cfg.finish = nil; runs = {}; saveCfg(); sendCfg(-1); say(pid, "finish line removed: start line is the finish again (loop)")
    elseif cmd == "finish" then
        MP.TriggerClientEventJson(pid, "kkLap_getpos", { r = tonumber(args[3]), what = "finish" })
    elseif cmd == "list" then
        if #cfg.checkpoints == 0 then say(pid, "no checkpoints") end
        if cfg.finish then say(pid, string.format("finish: %.1f %.1f %.1f r=%.1f (point-to-point)", cfg.finish.x, cfg.finish.y, cfg.finish.z, cfg.finish.r)) end
        for i, c in ipairs(cfg.checkpoints) do
            say(pid, string.format("%d: %.1f %.1f %.1f r=%.1f", i, c.x, c.y, c.z, c.r))
        end
    elseif cmd == "clear" then
        cfg.checkpoints = {}; cfg.finish = nil; runs = {}; board = {}; saveCfg(); saveBoard(); sendCfg(-1); say(pid, "checkpoints and finish cleared, best times wiped (laps.log keeps every lap)")
    elseif cmd == "undo" then
        table.remove(cfg.checkpoints); runs = {}; saveCfg(); sendCfg(-1); say(pid, "last checkpoint removed, " .. #cfg.checkpoints .. " left")
    elseif cmd == "reset" then
        runs = {}; sendCfg(-1); say(pid, "all timing reset")   -- sendCfg also clears every client's state
    elseif cmd == "race" then
        if tonumber(args[3]) then cfg.countdown = tonumber(args[3]) end
        startCountdown(name)
    elseif cmd == "autostart" then
        cfg.autoStart = (args[3] == "on"); saveCfg(); say(pid, "autostart " .. tostring(cfg.autoStart))
    elseif cmd == "line" and (args[3] == "paint" or args[3] == "draw") then
        cfg.lineStyle = args[3]; saveCfg(); sendCfg(-1); say(pid, "start line style " .. args[3])
    elseif cmd == "dj" and args[3] then
        cfg.djText = table.concat(args, " ", 3); saveCfg(); say(-1, "[DJ] " .. cfg.djText)
    elseif cmd == "dj" then
        say(-1, "[DJ] " .. tostring(cfg.djText))
    elseif cmd == "music" and args[3] == "stop" then
        MP.TriggerClientEventJson(-1, "kkLap_music", { stop = true }); say(-1, "music stopped by " .. name)
    elseif cmd == "music" and args[3] then
        -- /music <name> [volume 0-1] [once | <seconds>]   loops by default (DJ set); "once" plays through;
        -- a number of seconds is the fallback loop clock for games that give us no stoppable source
        local vol  = tonumber(args[4]) or cfg.musicVolume
        local loop = args[5] ~= "once"
        MP.TriggerClientEventJson(-1, "kkLap_music", { file = args[3], vol = vol, loop = loop, seconds = tonumber(args[5]) })
        say(-1, string.format("music: %s (%s, volume %.0f%%%s)", args[3], name, vol * 100, loop and ", on repeat" or ""))
        log("I", "music " .. args[3] .. " by " .. name)
    elseif cmd == "times" then
        local list = sortedBoard()
        if #list == 0 then say(pid, string.format("no times on the board yet (a driver needs %d laps first)", cfg.minLaps)) end
        local showPoints = #list >= cfg.pointsMinDrivers
        if #list > 0 and not showPoints then say(pid, string.format("points appear once %d drivers have times (%d so far)", cfg.pointsMinDrivers, #list)) end
        for i, it in ipairs(list) do
            if i > 10 then break end
            local pts = showPoints and string.format("  %d pts", pointsFor(it.e.bestMs, list[1].e.bestMs)) or ""
            say(pid, string.format("%d. %s  %s  %s  (%d laps, @%d km/h, resets %d)%s", i, it.name, fmt(it.e.bestMs),
                                   sectorsText(it.e.sectors or {}), it.e.laps or 0, it.e.kph or 0, it.e.resets or 0, pts))
        end
    elseif cmd == "announce" then
        cfg.announceSplits = (args[3] == "on"); saveCfg(); say(pid, "sector announcements to everyone: " .. tostring(cfg.announceSplits))
    elseif cmd == "markers" then
        cfg.markers = (args[3] == "on"); saveCfg(); sendCfg(-1); say(pid, "markers " .. tostring(cfg.markers))
    elseif cmd == "debug" then
        cfg.debug = (args[3] == "on"); saveCfg(); sendCfg(-1); say(pid, "debug " .. tostring(cfg.debug))
    elseif cmd == "set" and args[3] and tonumber(args[4]) then
        if cfg[args[3]] ~= nil and type(cfg[args[3]]) == "number" then
            cfg[args[3]] = tonumber(args[4]); saveCfg(); sendCfg(-1); say(pid, args[3] .. " = " .. args[4])
        else
            say(pid, "unknown numeric option " .. args[3])
        end
    else
        say(pid, "unknown /lap command, try /lap help")
    end
    return 1
end

-- ---------------------------------------------------------------- lifecycle
function onPlayerJoin(pid)
    sendCfg(pid)                          -- late joiner gets the current checkpoints
end
function onPlayerDisconnect(pid)
    for k in pairs(runs) do
        if k:match("^" .. pid .. ":") then runs[k] = nil end
    end
end

function onInit()
    loadCfg()
    loadBoard()
    MP.RegisterEvent("onChatMessage",      "onChatMessage")
    MP.RegisterEvent("onPlayerJoin",       "onPlayerJoin")
    MP.RegisterEvent("onPlayerDisconnect", "onPlayerDisconnect")
    MP.RegisterEvent("kkLap_cross",        "onCross")
    MP.RegisterEvent("kkLap_dbg",          "onClientDbg")
    MP.RegisterEvent("kkLap_posreply",     "onPosReply")
    MP.RegisterEvent("kkLap_getcfg",       "onGetCfg")
    MP.RegisterEvent("kkLap_arm",          "onArm")
    MP.RegisterEvent("kkLap_invalid",      "onInvalid")
    MP.RegisterEvent("kkLap_jump",         "onJump")
    MP.RegisterEvent("kkLap_tick",         "onTick")
    log("I", PLUGIN .. " v" .. VERSION .. " loaded on " .. MP.GetOSName())
end
