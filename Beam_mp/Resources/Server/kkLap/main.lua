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
local VERSION  = "0.5.2"   -- same number as the client zip it was built with
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
    announceLaps = true,     -- every lap goes to everyone in chat (v0.5.0)
    announceSplits = false,  -- every sector goes to everyone in chat too: noisy, off by default
    checkpoints  = {},
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
    MP.TriggerClientEventJson(pid, "kkLap_cfg", cfg)
    dbg("cfg sent to " .. pid)
end

-- ---------------------------------------------------------------- timing state
-- key = pid .. ":" .. vid   (split-screen = one pid, two vids, so vid is essential)
local runs  = {}
local timer = MP.CreateTimer()          -- server clock, seconds as float
local function nowMs() return math.floor(timer:GetCurrent() * 1000) end

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

function onCross(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or type(d) ~= "table" then log("E", "bad cross payload from " .. pid .. ": " .. tostring(data)); return end

    local key = pid .. ":" .. tostring(d.vid)
    local r   = runs[key] or { nextCp = 1, lapStart = nil, lastCp = nil, best = nil, laps = 0, sectors = {} }
    runs[key] = r
    local t   = nowMs()
    local n   = #cfg.checkpoints

    dbg(string.format("cross pid=%d vid=%s cp=%s expecting=%d", pid, tostring(d.vid), tostring(d.cp), r.nextCp))

    if d.cp ~= r.nextCp then
        -- wrong checkpoint: ignore, but tell the client in debug so a bad radius shows up
        MP.TriggerClientEventJson(pid, "kkLap_info", { vid = d.vid, msg = "wrong cp " .. tostring(d.cp) .. ", want " .. r.nextCp })
        return
    end

    if d.cp == 1 then
        -- start/finish line
        if r.lapStart then
            local lap = t - r.lapStart
            table.insert(r.sectors, t - r.lastCp)            -- last sector: previous cp -> finish line
            if lap < cfg.minLapMs then
                dbg("lap rejected, too short: " .. lap)
            else
                r.laps = r.laps + 1
                if not r.best or lap < r.best then r.best = lap end
                local name = MP.GetPlayerName(pid) or ("p" .. pid)
                local kph  = math.floor(tonumber(d.kph) or 0)
                MP.TriggerClientEventJson(-1, "kkLap_lap", { pid = pid, vid = d.vid, name = name, lapMs = lap, bestMs = r.best,
                                                            laps = r.laps, sectors = r.sectors, kph = kph })
                local line = string.format("%s  lap %d  %s  %s  best %s  @%d km/h",
                                           name, r.laps, fmt(lap), sectorsText(r.sectors), fmt(r.best), kph)
                log("I", "LAP " .. line)
                lapRecord(line)
                if cfg.announceLaps then say(-1, line) end
                local e = board[name]
                if not e or lap < e.bestMs then
                    board[name] = { bestMs = lap, sectors = r.sectors, laps = (e and e.laps or 0) + 1, kph = kph }
                else
                    e.laps = e.laps + 1
                end
                saveBoard()
            end
        else
            MP.TriggerClientEventJson(pid, "kkLap_info", { vid = d.vid, msg = "timing started" })
        end
        r.lapStart = t
        r.lastCp   = t
        r.sectors  = {}
        r.nextCp   = (n > 1) and 2 or 1
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
function onPosReply(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok or not d.pos then log("E", "bad pos reply: " .. tostring(data)); return end
    local cp = { x = d.pos.x, y = d.pos.y, z = d.pos.z, r = d.r or cfg.defaultRadius }
    table.insert(cfg.checkpoints, cp)
    saveCfg(); sendCfg(-1)
    say(pid, string.format("checkpoint %d marked at %.1f %.1f %.1f r=%.1f", #cfg.checkpoints, cp.x, cp.y, cp.z, cp.r))
end

-- ---------------------------------------------------------------- chat commands
-- Return 1 swallows the message so it doesn't show in chat.
function onChatMessage(pid, name, msg)
    if msg:sub(1, 4) ~= "/lap" then return end
    local args = {}
    for w in msg:gmatch("%S+") do table.insert(args, w) end
    local cmd = args[2] or "help"

    if cmd == "help" then
        say(pid, "/lap mark [radius]  - checkpoint at your car (1st = start/finish)")
        say(pid, "/lap times  - best lap per player, with sectors")
        say(pid, "/lap list | clear | undo | reset | debug on|off | markers on|off | announce on|off")
        say(pid, "/lap set minLapMs <n> | set defaultRadius <n> | set markerAlpha 0-255")
    elseif cmd == "mark" then
        -- ask the client for its current vehicle position; reply comes to onPosReply
        MP.TriggerClientEventJson(pid, "kkLap_getpos", { r = tonumber(args[3]) })
    elseif cmd == "list" then
        if #cfg.checkpoints == 0 then say(pid, "no checkpoints") end
        for i, c in ipairs(cfg.checkpoints) do
            say(pid, string.format("%d: %.1f %.1f %.1f r=%.1f", i, c.x, c.y, c.z, c.r))
        end
    elseif cmd == "clear" then
        cfg.checkpoints = {}; runs = {}; board = {}; saveCfg(); saveBoard(); sendCfg(-1); say(pid, "checkpoints cleared, best times wiped (laps.log keeps every lap)")
    elseif cmd == "undo" then
        table.remove(cfg.checkpoints); runs = {}; saveCfg(); sendCfg(-1); say(pid, "last checkpoint removed, " .. #cfg.checkpoints .. " left")
    elseif cmd == "reset" then
        runs = {}; sendCfg(-1); say(pid, "all timing reset")   -- sendCfg also clears every client's state
    elseif cmd == "times" then
        local list = sortedBoard()
        if #list == 0 then say(pid, "no laps yet") end
        for i, it in ipairs(list) do
            if i > 10 then break end
            say(pid, string.format("%d. %s  %s  %s  (%d laps, @%d km/h)", i, it.name, fmt(it.e.bestMs),
                                   sectorsText(it.e.sectors or {}), it.e.laps or 0, it.e.kph or 0))
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
    log("I", PLUGIN .. " v" .. VERSION .. " loaded on " .. MP.GetOSName())
end
