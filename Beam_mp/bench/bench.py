#!/usr/bin/env python3
"""kkLap bench: runs the REAL server plugin and the REAL client extension, with
every BeamMP/BeamNG call replaced by a stub, and the two stubs wired to each other.
A fake car is driven through the checkpoint spheres on a fake clock.

    python3 Beam_mp/bench/bench.py            run the scenarios
    python3 Beam_mp/bench/bench.py --break    prove the bench can fail (sabotages timing)

Server runs on Lua 5.3 (what Darren's BeamMP-Server v3.9.3 reports), client on LuaJIT (what BeamNG embeds).
Nothing here touches a real server. It is a model of the plumbing, not a measurement of it:
if the real game never calls onUpdate, this bench cannot know.
"""
import json, os, re, sys, shutil, tempfile, pathlib
from lupa import lua53, luajit21

ROOT   = pathlib.Path(__file__).resolve().parent.parent
SERVER = ROOT / "Resources/Server/kkLap/main.lua"
CLIENT = ROOT / "Resources/Client/kkLap/lua/ge/extensions/kkLap.lua"
SABOTAGE = "--break" in sys.argv

# ------------------------------------------------------------------ fake world
clock_ms   = [0]                         # server clock, we advance it by hand
positions  = {}                          # vid -> (x, y, z)
ve_cmds = []                             # (vid, lua string) queued into vehicle VMs
velocities = {}                          # vid -> (vx, vy, vz) m/s
own_vids   = []                          # vehicles the fake client owns
seat0_vid  = [None]                      # what be:getPlayerVehicle(0) returns
chat_out   = []                          # (pid, msg) from MP.SendChatMessage
toasts     = []                          # guihooks Message msgs
server_log = []                          # print() lines from the server
client_log = []                          # log() lines from the client
events_to_client = []                    # (pid, name, json) sent by server
dropped          = []                    # client events the server had no handler for
events_to_server = []                    # (name, data) sent by the client

# ------------------------------------------------------------------ server state
S = lua53.LuaRuntime(unpack_returned_tuples=True)
s_handlers = {}                          # event name -> lua function name

def s_register(ev, fn): s_handlers[ev] = fn
timers = {}                              # event name -> [interval_ms, next_fire_ms]  (MP.CreateEventTimer)
def s_create_event_timer(name, ms, *_): timers[name] = [ms, clock_ms[0] + ms]
def s_cancel_event_timer(name): timers.pop(name, None)
def fire_due_timers():
    for name, t in list(timers.items()):
        while name in timers and clock_ms[0] >= t[1]:
            t[1] += t[0]
            S.globals()[s_handlers[name]]()
def s_timer():
    return S.table(GetCurrent=lambda self: clock_ms[0] / 1000.0, Start=lambda self: None)
def s_trigger_client_json(pid, name, tbl):
    data = json.dumps(lua_to_py(tbl))
    # Real wire format server -> client is ":<NAME>:<DATA>" and the client splits it with
    # the Lua pattern ^:([^:]+):(.*)  (MPGameNetwork.lua handleEvents). Model that exactly.
    wire = ":" + name + ":" + data
    m = re.match(r"^:([^:]+):(.*)$", wire, re.S)
    if not m: return False, "malformed"
    wname, wdata = m.group(1), m.group(2)
    events_to_client.append((pid, wname, wdata))
    deliver_to_client(wname, wdata)
    return True, ""
def s_send_chat(pid, msg): chat_out.append((pid, msg))

def lua_to_py(v):
    if lupa_is_table(v):
        keys = list(v.keys())
        if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys)+1)):
            return [lua_to_py(v[k]) for k in sorted(keys)]
        return {str(k): lua_to_py(v[k]) for k in keys}
    return v
def lupa_is_table(v): return hasattr(v, "keys") and hasattr(v, "values") and not isinstance(v, dict)
def py_to_lua(R, v):
    if isinstance(v, dict):  return R.table_from({k: py_to_lua(R, x) for k, x in v.items()})
    if isinstance(v, list):  return R.table_from([py_to_lua(R, x) for x in v])
    return v

S.globals().MP = S.table(
    RegisterEvent=s_register, CreateTimer=s_timer, CreateEventTimer=s_create_event_timer, CancelEventTimer=s_cancel_event_timer, TriggerClientEventJson=s_trigger_client_json,
    SendChatMessage=s_send_chat, GetPlayerName=lambda pid: "Darren" if pid == 1 else "p%d" % pid,
    GetOSName=lambda: "Linux", GetPlayerCount=lambda: 1,
    GetPlayers=lambda: S.table_from({1: "Darren"}),           # BeamMP: { [id] = name } (TLuaEngine.cpp Lua_GetPlayers)
    GetPlayerIdentifiers=lambda pid: S.table(beammp="42", ip="1.2.3.4") if pid == 1 else S.table(ip="5.6.7.8"),
)
S.globals().Util = S.table(
    JsonEncode=lambda t: json.dumps(lua_to_py(t)),
    JsonDecode=lambda s: py_to_lua(S, json.loads(s)),
)
S.globals().print = lambda *a: server_log.append(" ".join(str(x) for x in a))

# ------------------------------------------------------------------ client state
C = luajit21.LuaRuntime(unpack_returned_tuples=True)
c_handlers = {}

def c_trigger_server(name, data):
    # Real wire format client -> server is "E:<NAME>:<DATA>" and TServer.cpp HandleEvent
    # splits at the first ':' after position 2. Model that exactly.
    wire = "E:" + name + ":" + data
    sep = wire.find(":", 2)
    wname, wdata = wire[2:sep], wire[sep+1:]
    events_to_server.append((wname, wdata))
    fn = s_handlers.get(wname)
    if not fn:
        dropped.append(wname)                  # the real server drops it silently
        return None
    return S.globals()[fn](1, wdata)          # the fake client is player 1
def c_add_handler(name, func, _n=None): c_handlers[name] = func
def deliver_to_client(name, data):
    if name in c_handlers: c_handlers[name](data)

class Veh:
    def __init__(self, vid): self.vid = vid
    def getPosition(self):
        x, y, z = positions[self.vid]; return C.table(x=x, y=y, z=z)
    def getID(self): return self.vid
    def getDirectionVector(self): return C.table(x=1.0, y=0.0, z=0.0)   # car points along +x
    def queueLuaCommand(self, cmd): ve_cmds.append((self.vid, cmd))
    def getVelocityXYZ(self):
        return velocities.get(self.vid, (0.0, 0.0, 0.0))    # m/s, three values like the game
    def getJBeamFilename(self): return jbeams.get(self.vid, "pickup")   # BeamNG GE; BeamMP's walker is "unicycle"
jbeams = {}                                                  # vid -> jbeam name (v0.24)

C.globals().TriggerServerEvent = c_trigger_server
C.globals().AddEventHandler    = c_add_handler
C.globals().MPVehicleGE = C.table(getOwnMap=lambda: C.table_from({v: C.table() for v in own_vids}))
C.globals().be = C.table(
    getObjectByID=lambda self, vid: Veh(vid) if vid in positions else None,
    getPlayerVehicle=lambda self, seat: Veh(seat0_vid[0]) if seat0_vid[0] else None,
)
def ui(name, t):
    if name == "Message":               toasts.append(t["msg"])
    elif name == "ScenarioFlashMessage": flashes.append(t[1][1])      # {{msg, ttl, sound, big}}
    elif name == "ScenarioRealtimeDisplay": realtime[0] = t["msg"]
C.globals().guihooks = C.table(trigger=ui)
flashes  = []
realtime = [""]
C.globals().log = lambda lvl, origin, msg: client_log.append("[%s] %s" % (lvl, msg))
C.globals().vec3 = lambda t: t                  # vec3(x) wrapper: the stub tables already have x,y,z
C.globals().jsonEncode = lambda t: json.dumps(lua_to_py(t))
C.globals().jsonDecode = lambda s: py_to_lua(C, json.loads(s))
# Audio stub (v0.22): createSource gives no source object, so the client falls back to
# playOnce, which works unless Engine.Audio.fail > 0 (then it throws and counts down).
# imgui stub: just enough of ui_imgui for the rules window and the HUD to run their draw
# code; every Text call is recorded so the bench can see what reached the screen.
# v0.24: Text, TextWrapped and TextColored are printf-style like the real ImGui (string.format
# with no arguments: a lone "%s" throws, "%%" prints "%"); failText makes every text call throw
# (an error between Begin and End); srcId makes createSource hand out a source id whose object
# lookup then fails (the leak); every deleteSource id is recorded.
# v0.26: SameLine is recorded as a drawn entry 'sameline'; the lights' draw-list path records each
# filled circle as 'circle r,g,b'; noDrawList makes GetWindowDrawList throw to prove the text fallback.
C.execute("""
scenetree = { findObjectById = function(id) return nil end }
-- v0.27: core_vehicles.getModel(key).model.Brand/Name is how the client names a car (BeamNG GE, from memory);
-- only etk800 is known here, every other key comes back empty so the client falls back to the key itself
core_vehicles = { getModel = function(key)
    local known = { etk800 = { Brand = 'ETK', Name = '800-Series' } }
    return known[key] and { model = known[key] } or {}
end }
Engine = { Audio = { fail = 0, plays = {}, srcId = nil, deleted = {},
    createSource = function(ch, path) return Engine.Audio.srcId end,
    deleteSource = function(id) table.insert(Engine.Audio.deleted, id) end,
    playOnce = function(ch, path, opts)
        if Engine.Audio.fail > 0 then Engine.Audio.fail = Engine.Audio.fail - 1; error('stub: audio not ready') end
        table.insert(Engine.Audio.plays, path)
    end } }
local drawn = {}
local shadows = {}   -- v0.27: every pure-black TextColored (the drop shadow pass) lands here as { text, at = index of the coloured entry that follows }, not in drawn
local function pf(t) if ui_imgui.failText then error('stub: text call failed') end; return string.format(t) end
ui_imgui = { drawn = drawn, shadows = shadows, scale = 1, colours = {}, failBegin = nil, failText = false, noDrawList = false, noCursor = false,   -- colours[i] = 'r,g,b' of drawn[i] (TextColored)
    Cond_Always = 1, Cond_FirstUseEver = 4,
    WindowFlags_NoTitleBar = 1, WindowFlags_NoResize = 2, WindowFlags_NoMove = 4, WindowFlags_NoBackground = 128,
    WindowFlags_NoInputs = 512, WindowFlags_NoFocusOnAppearing = 4096,
    ImVec2 = function(x, y) return { x = x, y = y } end,
    ImVec4 = function(r, g, b, a) return { r, g, b, a } end,
    GetIO = function() return { DisplaySize = { x = 960, y = 1080 } } end,   -- half of a 1920 screen: split screen
    SetNextWindowPos = function() end, SetNextWindowSize = function() end, SetNextWindowBgAlpha = function() end,
    Begin = function(name)
        if ui_imgui.failBegin == name then error('stub: imgui call mismatch') end   -- bench sabotage of one window
        table.insert(drawn, 'begin ' .. name)
    end,
    End = function() table.insert(drawn, 'end') end,
    GetWindowHeight = function() return 60 end,
    SetWindowFontScale = function(f) ui_imgui.scale = f end,
    Text = function(t) table.insert(drawn, 'x' .. ui_imgui.scale .. ' ' .. pf(t)) end,
    TextColored = function(c, t)
        if c[1] == 0 and c[2] == 0 and c[3] == 0 then table.insert(shadows, { text = 'x' .. ui_imgui.scale .. ' ' .. pf(t), at = #drawn + 1, cursor = ui_imgui.cursor }); return end
        table.insert(drawn, 'x' .. ui_imgui.scale .. ' ' .. pf(t))
        ui_imgui.colours[#drawn] = string.format('%.2f,%.2f,%.2f', c[1], c[2], c[3])
    end,
    -- v0.27: the cursor for the drop shadow; noCursor removes GetCursorPos to prove the plain fallback
    cursor = '0,0',
    GetCursorPos = function() if ui_imgui.noCursor then error('stub: GetCursorPos missing') end; return { x = 10, y = 20 } end,
    SetCursorPos = function(v) ui_imgui.cursor = v.x .. ',' .. v.y end,
    TextWrapped = function(t) table.insert(drawn, 'w ' .. pf(t)) end, Separator = function() end, Button = function() return false end,
    SameLine = function() table.insert(drawn, 'sameline') end, Dummy = function() end,
    GetWindowDrawList = function() if ui_imgui.noDrawList then error('stub: no draw list') end; return {} end,
    GetCursorScreenPos = function() return { x = 0, y = 0 } end,
    GetColorU322 = function(c) return c end,
    ImDrawList_AddCircleFilled = function(dl, centre, r, col, segs) table.insert(drawn, string.format('circle %.2f,%.2f,%.2f', col[1], col[2], col[3])) end,
}
""")
AUDIO = C.globals().Engine["Audio"]
DRAWN = C.globals().ui_imgui["drawn"]
COLOURS = C.globals().ui_imgui["colours"]
SHADOWS = C.globals().ui_imgui["shadows"]

# ------------------------------------------------------------------ load real code
work = pathlib.Path(tempfile.mkdtemp(prefix="kklap-bench-"))
(work / "Resources/Server/kkLap").mkdir(parents=True)
shutil.copy(SERVER, work / "Resources/Server/kkLap/main.lua")
os.chdir(work)                                   # so io.open("Resources/Server/kkLap/...") resolves
S.execute(SERVER.read_text())
S.globals().onInit()

M = C.execute(CLIENT.read_text())
M.onExtensionLoaded()                            # client asks server for config here

# ------------------------------------------------------------------ helpers
def chat(msg): return S.globals()[s_handlers["onChatMessage"]](1, "Darren", msg)
def console(line): return S.globals()[s_handlers["onConsoleInput"]](line)   # a line typed in the server console (v0.25)
def kklog(): return (work / "Resources/Server/kkLap/kkLap.log").read_text().splitlines()
def chat_as(pid, name, msg): return S.globals()[s_handlers["onChatMessage"]](pid, name, msg)
timer_frames = []   # (clock_ms, [(text, colour)] drawn in the ##kkLapTimer window, or None if not drawn) per frame (v0.23)
hud_seen = set()    # every text that was on the HUD queue at the end of any frame (v0.23)
HUD = M.hudState
def hud_items(): return [(it["text"], bool(it["big"])) for it in HUD["items"].values()]
def tick(dt_s=0.05, ms=None):
    """advance the clock and run one client frame"""
    clock_ms[0] += int((ms if ms is not None else dt_s*1000))
    fire_due_timers()
    m = len(DRAWN)
    M.onUpdate(dt_s)
    lines = None
    for i in range(m + 1, len(DRAWN) + 1):
        d = DRAWN[i]
        if d == "begin ##kkLapTimer": lines = []
        elif lines is not None:
            if d == "end" or d.startswith("begin "): break
            lines.append((d, COLOURS[i]))
    timer_frames.append((clock_ms[0], lines))
    for t, _ in hud_items(): hud_seen.add(t)
    M.onPreRender(dt_s)                           # markers code runs every frame too (no ffi here: no-op draw)
def drive(vid, path, step_ms=100):
    """move vid through a list of points, one frame per point"""
    for p in path:
        positions[vid] = p; tick(ms=step_ms)
def laps(): return [json.loads(d) for (pid, n, d) in events_to_client if n == "kkLap_lap"]
def fmt(ms): return "%d:%06.3f" % (ms // 60000, (ms % 60000) / 1000)          # same as the client's M.fmt
def tframes(t0, t1): return [f for (t, f) in timer_frames if t0 < t <= t1]     # timer window frames in (t0, t1]
def texts(f): return [d for d, _ in f] if f else []
def gap_line(ms):                                                              # what the timer should draw for a deltaMs
    return ("x2 " + ("+" if ms > 0 else "-") + "%.2f" % (abs(ms) / 1000), "1.00,0.35,0.35" if ms > 0 else "0.35,1.00,0.35")
RUN = re.compile(r"^x3 \d+:\d\d\.\d{3}$")                                     # running clock, big
def is_gap(d): return re.match(r"^x2 [+-]\d", d) is not None                        # a gap line (v0.26: the sector line is x2 too)
VER = re.search(r"M\.VERSION\s*=\s*'([^']+)'", CLIENT.read_text()).group(1)        # the version under test
def go_events(): return [json.loads(d) for (pid, n, d) in events_to_client if n == "kkLap_go"]
def crosses(since, vid): return [json.loads(d) for n, d in events_to_server[since:] if n == "kkLap_cross" and json.loads(d).get("vid") == vid]
def infos(since, vid): return [json.loads(d)["msg"] for p, n, d in events_to_client[since:] if n == "kkLap_info" and json.loads(d).get("vid") == vid]
def said(since, text, pid=None): return [m for p, m in chat_out[since:] if text in m and (pid is None or p == pid)]
def race_start(vids, cp1=(0, 0, 0)):
    """v0.26 standing start, honk only: park vids in the start bubble, wait out the server's 10 s honk-ignore
    window after the last GO, every car honks, the countdown runs to GO. Nobody's clock runs yet: the
    first of them to drive out of the bubble starts the clock of the whole race. Returns the GO clock (ms)."""
    saved = {v: velocities.get(v, (27.78, 0.0, 0.0)) for v in vids}
    for i, v in enumerate(vids):
        positions[v] = (cp1[0] + 2 * i, cp1[1], cp1[2]); velocities[v] = (0.0, 0.0, 0.0)
    g = go_events()
    while g and clock_ms[0] - g[-1]["t"] < 10100: tick()
    for _ in range(3): tick()
    for v in vids: M.onHorn(v)
    n0 = len(g)
    for _ in range(1400):
        tick()
        if len(go_events()) > n0: break
    if len(go_events()) <= n0: check(False, "(setup) race_start: no GO after the honks of %s" % (vids,))
    for v in vids: velocities[v] = saved[v]
    return go_events()[-1]["t"]

failures = []
def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond: failures.append(what)

# ------------------------------------------------------------------ scenario v0.25a: admin commands from the server console only
# Darren 30/10: "i need ONLY admin commands for server side only terminal". BeamMP fires
# onConsoleInput with each console line; a returned string is printed, nil = not ours.
G0 = S.globals()
check(G0.cfg["adminChat"] == True, "v0.27: adminChat is on by default (Darren: admin controls in chat and in the console)")
check(G0.cfg["minLaps"] is None and G0.cfg["maxLaps"] == 0 and G0.cfg["timeoutS"] == 30, "v0.28: defaults: no minLaps any more, maxLaps 0 (no limit), timeoutS 30")
check(console("lap set maxLaps 99") == "maxLaps = 99", "v0.27: (setup) maxLaps 99 for the scenarios below, which drive 3+ laps in one run; the v0.27 scenario puts 2 back")
check(console("lap adminchat off").startswith("adminchat off"), "v0.27: (setup) the kill switch off: the v0.25 console-only checks below hold with it")
mark = len(chat_out); cd0 = G0.cfg["countdown"]
check(chat("/set countdown 7") == 1 and G0.cfg["countdown"] == cd0 and any(p == 1 and "admin commands run from the server console only" in m for p, m in chat_out[mark:]),
      "v0.25: /set countdown 7 in chat from pid 1 is refused with the console-only line, cfg unchanged")
r = console("lap set countdown 7")
check(r == "countdown = 7" and G0.cfg["countdown"] == 7, "v0.25: lap set countdown 7 from the console changes cfg and returns the confirmation (%r)" % r)
check(any("[KKLAP][CONSOLE] lap set countdown 7" in l for l in kklog()), "v0.25: the console line is in kkLap.log as [CONSOLE] lap set countdown 7")
check(console("status") is None and console("kick Bob") is None and console("") is None, "v0.25: a console line that is not ours (BeamMP's own status, kick) returns nil")
h = console("lap help")
check(isinstance(h, str) and "adminchat" in h and "lap mark <player>" in h and "/mark" not in h, "v0.25: lap help on the console lists the admin commands in console spelling, adminchat too")
check(console("LAP SET countdown 5") == "countdown = 5" and console("/lap set countdown 6") == "countdown = 6" and console("kklap set countdown 5") == "countdown = 5",
      "v0.25: lap, /lap and kklap all work, any case")
check("unknown kkLap command" in console("lap frobnicate") and "lap help" in console("lap frobnicate"), "v0.25: lap <unknown word> is answered with the way to help")
r = console("lap mark")
check(r.startswith("needs a player name: lap mark <name>"), "v0.25: lap mark with no name says it needs a player name (%r)" % r)
E0 = len(events_to_client); r = console("lap mark Darren")
check(any(p == 1 and n == "kkLap_getpos" and json.loads(d).get("what") == "mark" for p, n, d in events_to_client[E0:]) and "asked Darren" in r,
      "v0.25: lap mark Darren sends kkLap_getpos to pid 1, found by name through MP.GetPlayers")
check("no player called Zed" in console("lap start Zed") and "Darren" in console("lap start Zed"), "v0.25: an unknown name is refused with who is on")
check(len(G0.cfg["checkpoints"]) == 0, "v0.25: (setup) no car in seat 0, so nothing was marked")
mark = len(chat_out); chat_as(2, "Bob", "/help")
bob = [m for p, m in chat_out[mark:] if p == 2]
check(any("admins: use the server console" in m for m in bob) and not any("/mark" in m or "/set" in m for m in bob),
      "v0.25: chat /help lists no admin commands while adminChat is off, says admins: use the server console")
mark = len(chat_out)
check(chat("/times") == 1 and any("no times on the board yet" in m for _, m in chat_out[mark:]), "v0.25: player commands still work in chat (/times)")
mark = len(chat_out); chat("/adminchat on"); chat("/rules add sneaky"); chat("/dj sneaky")
check(G0.cfg["adminChat"] == False and not any("sneaky" in r for r in G0.cfg["rules"].values()) and G0.cfg["djText"] != "sneaky"
      and len([m for p, m in chat_out[mark:] if p == 1 and "console only" in m]) == 3,
      "v0.25: /adminchat, /rules add and /dj <text> from chat are refused too")
r = console("lap adminchat on")
check(G0.cfg["adminChat"] == True and "allowed for admins" in r and json.load(open("Resources/Server/kkLap/config.json"))["adminChat"] == True,
      "v0.25: lap adminchat on from the console turns chat admin commands on, saved (%r)" % r)
mark = len(chat_out); chat("/set countdown 6")
check(G0.cfg["countdown"] == 6 and any(p == 1 and "countdown = 6" in m for p, m in chat_out[mark:]), "v0.25: after lap adminchat on, chat /set works for an admin")
chat("/set countdown 5")

# ------------------------------------------------------------------ scenario 0: empty track still proves the loop is alive
# v0.3.0 put the 5 s alive line below the "no checkpoints" exit, so the live test could not
# see onUpdate running until a checkpoint existed (26/10). Must fire with zero checkpoints.
own_vids[:] = [100, 101]; seat0_vid[0] = 100
positions[100] = (0, 0, 0);   positions[101] = (500, 500, 0)
M.onVehicleSpawned(100); M.onVehicleSpawned(101)   # the game calls this per spawn; we load our vehicle file there
for _ in range(120): tick()                      # 6 s of frames, no checkpoints yet
check(any("alive, own vehicles: 100,101" in l for l in client_log), "alive line fires with zero checkpoints")

# ------------------------------------------------------------------ scenario 1: mark a track from the car
own_vids[:] = [100, 101]; seat0_vid[0] = 100
positions[100] = (0, 0, 0);   positions[101] = (500, 500, 0)
chat("/lap mark")                                # cp1 at car (0,0,0): server asks, GE asks the car's Lua
check(any("kkLapVE.reportSurface()" in c for v, c in ve_cmds if v == 100), "/lap mark asks the car's Lua what the wheels are on")
mark_chat = len(chat_out)
M.onSurface(100, False, "FL=GRASS,FR=GRASS,RL=GRASS,RR=GRASS")      # the car answers: grass
check(any("must be on tarseal" in m and "GRASS" in m for _, m in chat_out[mark_chat:]), "start/finish on grass is refused and says what the wheels are on")
chat("/lap mark"); M.onSurface(100, True, "FL=ASPHALT,FR=ASPHALT,RL=ASPHALT,RR=ASPHALT")   # tarseal: accepted
positions[100] = (100, 0, 0); chat("/lap mark 10"); M.onSurface(100, False, "FL=GRASS")   # cp2 on grass is fine, only the start is fussy
cfgfile = pathlib.Path("Resources/Server/kkLap/config.json")
check(cfgfile.exists(), "/lap mark round trip wrote config.json")
cfg = json.load(open(cfgfile)) if cfgfile.exists() else {"checkpoints": [], "defaultRadius": None}
if not cfg["checkpoints"]: cfg["checkpoints"] = [{"r": None}, {"r": None}]
check(len(cfg["checkpoints"]) == 2, "two checkpoints saved to config.json")
check(cfg["checkpoints"][1]["r"] == 10, "/lap mark 10 sets radius 10")
check(cfg["checkpoints"][0].get("hx") == 1.0 and cfg["checkpoints"][0].get("surface", "").startswith("FL=ASPHALT"), "start checkpoint stores the car's heading and surface")
check(any("start line drawn as flat cells, total 20" in l or "start line painted" in l for l in client_log), "client builds the start line from the config (paint, or 20 flat cells without a game)")
check(cfg["checkpoints"][0]["r"] == cfg["defaultRadius"], "/lap mark uses defaultRadius")
check(any("checkpoint 2 marked" in m for _, m in chat_out), "chat confirms the mark")

# ------------------------------------------------------------------ scenario 2: one clean lap, vid 100
velocities[100] = (27.78, 0.0, 0.0)              # 100 km/h along x, so every crossing reports it
positions[100] = (50, 0, 0); tick()              # start between the two spheres, outside both
before = len(laps())
drive(100, [(0, 0, 0)])                          # into the start bubble: at the line, clock at zero (v0.26)
race_start([100])                                # honk -> countdown -> GO: still zero
drive(100, [(50, 0, 0)]); t_start = clock_ms[0]  # the first (only) car out: the clock starts now
drive(100, [(100, 0, 0)])                        # enter cp2 (split)
drive(100, [(50, 0, 0)] * 49)                    # cruise ~4.9 s so the lap clears minLapMs
drive(100, [(0, 0, 0)])                          # enter cp1 again: lap (and the next one starts at once, drive-through)
expected = clock_ms[0] - t_start
got = laps()
check(len(got) == before + 1, "one lap event after crossing cp1 -> cp2 -> cp1")
if got:
    lap = got[-1]
    check(lap["vid"] == 100 and lap["lapMs"] == (expected if not SABOTAGE else -1),
          "lap time equals server clock from driving out of the bubble to the line (%d ms)" % expected)
    check(lap["name"] == "Darren" and lap["laps"] == 1, "lap carries player name and lap count")
    check(sum(lap["sectors"]) == lap["lapMs"], "sector times add up to the lap time")
    check(lap["kph"] == 100, "lap event carries entry speed (100 km/h from 27.78 m/s)")
splits = [json.loads(d) for (pid, n, d) in events_to_client if n == "kkLap_split"]
check(splits and splits[-1]["kph"] == 100 and splits[-1]["sectorMs"] > 0, "split event carries sector time and speed")
check(not any(t.startswith("S1") for t in toasts) and not any(t.startswith("S1") for t in hud_seen),
      "no sector toast and no sector text on the HUD queue any more (v0.23: the timer window has the gap)")
check(not any(f.startswith("S1") or f.startswith("LAP") for f in flashes), "no sector or lap flash either (one clock, one place)")
t_lap1 = timer_frames[-1][0]
nxt = [f for t, f in timer_frames if t <= t_lap1]
check(any(f and RUN.match(texts(f)[0]) for f in nxt[-40:]) and not any(f and "x1 best" in " ".join(texts(f)) for f in nxt),
      "timer window showed the running clock big during lap 1, and no best line before a lap was set")
check(not any(f and any(is_gap(d) for d in texts(f)) for f in nxt), "first split (no reference yet) shows no gap line")
check("TIMING STARTED" in flashes, "timing started flashed")
check(realtime[0] == "", "the Race Realtime Display app clock stays empty while the imgui timer runs (never two clocks)")
check(any(pid == -1 and "Darren  lap 1" in m for pid, m in chat_out), "lap announced to everyone in chat")
check(pathlib.Path("Resources/Server/kkLap/laps.log").exists(), "laps.log written")
mark = len(chat_out); chat("/lap times")
check(any("1. Darren" in m and "standing 0:05.100  rolling -" in m for _, m in chat_out[mark:]), "v0.27: one lap is enough for the board (minLaps 1): /lap times lists it as the standing lap, no rolling yet")
check(not any("one more lap and your best counts" in m for _, m in chat_out), "v0.27: the driver is not told to do one more lap")
check(lap["lapType"] == "standing" and lap["car"] == "pickup", "v0.27: the first lap out of the bubble is typed standing and names the car (the jbeam key, nothing known about a pickup)")
# second lap with one physics reset in it: counted, allowed
drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 20)
M.onVehReset(100)                                              # Insert: car did not move -> physics reset
check(any("physics reset #1 counted" in l for l in client_log), "physics reset in place is counted, lap goes on")
drive(100, [(50, 0, 0)] * 30 + [(0, 0, 0)])
lap2 = laps()[-1]
check(lap2["laps"] == 2 and lap2["resets"] == 1, "lap 2 counts and carries resets=1")
check(lap2.get("deltaMs") == lap2["lapMs"] - 5100, "lap 2 carries the delta to the previous best (+%d ms)" % (lap2["lapMs"] - 5100))
check(lap2["lapType"] == "rolling" and json.load(open("Resources/Server/kkLap/times.json"))["id:42"]["bestRollingMs"] == lap2["lapMs"], "v0.27: lap 2 (drive-through) is rolling and goes on the board as the best rolling lap")
splits = [json.loads(d) for (pid, n, d) in events_to_client if n == "kkLap_split"]
check(splits[0].get("deltaMs") is None and splits[1].get("deltaMs") is not None, "first sector has no delta, the second time through it does")
after1 = [f for t, f in timer_frames if t > t_lap1]
check(after1 and texts(after1[0])[0] == "x3 LAP  0:05.100" and texts(after1[0])[-1] == "x1 best 0:05.100   lap 1",
      "after lap 1 the timer window shows 'LAP  0:05.100' big and the best line with the lap count appears")
g = gap_line(splits[1]["deltaMs"])
check(any(f and g in f for f in after1), "the lap-2 sector gap is in the timer window with the server's sign and colour (%s)" % (g,))
check(not any(t.startswith("S1") or "lap 2" in t for t in toasts), "no toast for lap 2 or its sector either")
mark = len(chat_out); chat("/lap times")
check(any("1. Darren" in m and "resets 0" in m for _, m in chat_out[mark:]), "/lap times lists the best lap after 2 laps (lap 1, which had no resets)")
check("id:42" in json.load(open("Resources/Server/kkLap/times.json")), "board is keyed by the BeamMP account id, not the (guest) name")
check(pathlib.Path("Resources/Server/kkLap/times.json").exists(), "times.json written once the driver has 2 laps")
check(any("points appear once 3 drivers" in m for _, m in chat_out[mark:]), "points wait for 3 drivers")
check(S.globals().pointsFor(60000, 60000) == 100 and S.globals().pointsFor(66000, 60000) == 90 and S.globals().pointsFor(120000, 60000) == 50,
      "points: 100 at the top, 90 at 10% slower, 50 at double")
# third lap with a position reset (recover moved the car 30 m): must not count
before = len(laps()); mark = len(chat_out)
drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 20)
positions[100] = (50, 30, 0); M.onVehReset(100)                # recover: car jumped 30 m sideways
check(any(pid == -1 and "lap will not count" in m and "position reset" in m for pid, m in chat_out[mark:]), "position reset is announced to everyone at once")
drive(100, [(50, 0, 0)] * 30 + [(0, 0, 0)])
check(len(laps()) == before and any("not counted" in m for _, m in chat_out[mark:]), "the invalidated lap is not counted at the line")
# teleport with no reset event at all (map teleport): caught by the frame-jump detector after 1 s
before = len(laps()); mark = len(chat_out)
drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 10)
positions[100] = (400, 400, 0); tick(); positions[100] = (50, 0, 0)
for _ in range(25): tick()
check(any("moved" in m and "in one frame" in m for _, m in chat_out[mark:]), "a 500 m jump with no reset event invalidates the lap")
drive(100, [(50, 0, 0)] * 40 + [(0, 0, 0)])
check(len(laps()) == before, "that lap is not counted either")

# ------------------------------------------------------------------ scenario 2b: marker colours follow the lap (pure function, no ffi needed)
st = lambda **k: C.table(**k)
check(M.cpStyle(1, None, 2) == "todo", "no state yet: plain markers")
check(M.cpStyle(2, st(nextCp=2, started=True, flashCp=1, flashT=1.0), 2) == "next", "next checkpoint is highlighted")
check(M.cpStyle(1, st(nextCp=2, started=True, flashCp=1, flashT=1.0), 2) == "flash", "checkpoint just crossed flashes")
check(M.cpStyle(1, st(nextCp=2, started=True, flashCp=1, flashT=0.0), 2) == "done", "after the flash it is drawn as done")
check(M.cpStyle(2, st(nextCp=1, started=True, flashCp=2, flashT=0.0), 2) == "done", "heading for the line, every other cp is done")
check(M.cpStyle(2, st(nextCp=1, started=False, flashCp=None, flashT=0), 2) == "todo", "before the first crossing nothing is done")

# ------------------------------------------------------------------ scenario 2c: back into the start bubble mid-lap = fresh start, always
# vid 100 has just crossed the line (nextCp 2). It wanders off and comes back to the start without cp2.
mark_s = len(server_log); mark_c = len(client_log); mark_e = len(events_to_client)
drive(100, [(50, 0, 0), (0, 0, 0)])
check(any("partial lap dropped" in l for l in client_log[mark_c:]) and any("at the line pid=1 vid=100 (partial lap dropped)" in l for l in server_log[mark_s:])
      and infos(mark_e, 100) == ["at the line"],
      "driving back into the start bubble mid-lap drops the partial lap on both sides: at the line, clock at zero (v0.26)")
before = len(laps())
race_start([100])                                              # honk to go again
drive(100, [(50, 0, 0)]); t_restart = clock_ms[0]              # out: the clock starts
drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])
got = laps()[before:]
check(len(got) == 1 and got[0]["lapMs"] == clock_ms[0] - t_restart, "the lap after a restart is timed from driving out after the new GO, not from the first crossing")
# honk works from that state too (mid-lap, back in the bubble)
for _ in range(200): tick()
mark = len(chat_out); positions[100] = (0, 0, 0); velocities[100] = (0.0, 0.0, 0.0); tick(); M.onHorn(100)
check(any(pid == 1 and "race start in" in m for pid, m in chat_out[mark:]), "honk in the bubble arms a countdown whatever state the car was in (v0.27: said to the racer, not to everyone)")
for _ in range(130): tick()
velocities[100] = (27.78, 0.0, 0.0)

# ------------------------------------------------------------------ scenario 2d: crossing the start backwards is ignored
mark_c = len(client_log)
positions[100] = (50, 0, 0); tick()                            # out after the GO above: its clock starts here (v0.26)
mark_e = len(events_to_client)
velocities[100] = (-27.78, 0.0, 0.0)                           # driving back along -x into the start (heading was +x)
drive(100, [(0, 0, 0)])
check(any("backwards, ignored" in l for l in client_log[mark_c:]), "entering the start against its heading says WRONG WAY and sends nothing")
check(not any(n == "kkLap_info" and "timing started" in d for (p, n, d) in events_to_client[mark_e:]), "no timing started from a backwards crossing")
velocities[100] = (27.78, 0.0, 0.0); positions[100] = (50, 0, 0); tick()

# ------------------------------------------------------------------ scenario 3: second vehicle on the same client times separately
# vid 101 jumps sphere to sphere with NO frame outside in between. That is the case
# the first bench run caught: the inside flag leaked across checkpoints.
positions[100] = (50, 0, 0)
drive(101, [(0, 0, 0)]); race_start([101])                     # at the line, honk, GO (car 100 is outside the bubble: 101 honks alone)
drive(101, [(100, 0, 0)]); drive(101, [(50, 0, 0)] * 70); drive(101, [(0, 0, 0)])   # out and into cp2 in ONE frame, cruise, lap
got = laps()
check(got[-1]["vid"] == 101 and got[-1]["laps"] == 1, "vid 101 gets its own lap count (split-screen for free)")

# ------------------------------------------------------------------ scenario 4: too-short lap is rejected
positions[101] = (500, 500, 0)                   # 101 ended its lap in the start bubble: out of the way, or the honk below waits on it (v0.26)
chat("/lap reset")                               # server state AND client state back to cp1
positions[100] = (50, 0, 0); tick()
drive(100, [(0, 0, 0)]); race_start([100])                     # at the line, honk, GO (v0.26)
n = len(laps()); mark = len(server_log)
drive(100, [(50, 0, 0), (100, 0, 0), (50, 0, 0), (0, 0, 0)])   # out (the clock starts), cp2, the line: a full lap in 300 ms
check(len(laps()) == n, "lap under minLapMs is rejected")
check(any("lap rejected" in l for l in server_log[mark:]), "server log says why it was rejected")

# ------------------------------------------------------------------ scenario 5: /lap clear wipes state on both sides
chat("/lap clear")
mark = len(client_log)
drive(100, [(50, 0, 0), (0, 0, 0)])
check(not any("entered cp" in l or "at the line" in l for l in client_log[mark:]), "after /lap clear the client reports no crossings and no bubble")
check(any("[KKLAP][C1]" in l for l in server_log), "client debug lines reach the server log with the [C<pid>] tag")
check((work / "Resources/Server/kkLap/kkLap.log").exists(), "kkLap.log file is written")
check(not dropped, "no client event was dropped by the server for an unknown name (first: %s)" % (dropped[:1] or "-"))

# ------------------------------------------------------------------ scenario 7: two cars sit on the line -> countdown -> GO -> both laps count from GO
positions[100] = (0, 0, 0); chat("/lap mark"); M.onSurface(100, True, "FL=ASPHALT")
positions[100] = (100, 0, 0); chat("/lap mark 10"); M.onSurface(100, True, "FL=ASPHALT")   # track again after /lap clear
chat("/lap reset")
positions[100] = (0, 0, 0); positions[101] = (2, 0, 0)        # both parked in the start bubble
velocities[100] = (0.0, 0.0, 0.0); velocities[101] = (0.0, 0.0, 0.0)
for _ in range(220): tick()                                   # the server ignores honks for 10 s after a GO (scenario 4 had one)
chat("/lap reset"); mark_e = len(events_to_client)
positions[100] = (0, 0, 0); positions[101] = (2, 0, 0)
mark_chat = len(chat_out); mark_cl = len(client_log)
for _ in range(10): tick()
check(infos(mark_e, 100) == ["at the line"] and infos(mark_e, 101) == ["at the line"],
      "v0.26: two cars parked in the start bubble are at the line, nothing started")
check(timer_frames[-1][1] is not None and texts(timer_frames[-1][1]) == ["x3 0:00.000", "x1 in the start bubble: honk to race"],
      "v0.26: the timer window shows 0:00.000 and the honk hint while parked (%s)" % texts(timer_frames[-1][1]))
M.onHorn(100)                                                 # vehicle Lua reports the horn; car 100 is on the line
check(any("honked" in l and "countdown" in l for l in client_log[mark_cl:]), "a honk is sent to the server, which decides from its bubble roster")
check(any(pid == 1 and "ready  (1 of 2 in the start bubble" in m for pid, m in chat_out[mark_chat:]), "with two cars in the bubble one honk is not enough: 1 of 2 ready (v0.27: to the racers)")
check(any(n == "kkLap_hud" for (_, n, _) in events_to_client) and any(t.startswith("Darren ready  (1 of 2") and not big for t, big in hud_items()),
      "the 'ready 1 of 2' line reaches the on-screen HUD (kkLap_hud event lands in the queue)")
check(not any("race start in" in m for _, m in chat_out[mark_chat:]), "no countdown until everyone in the bubble has honked")
M.onHorn(101)                                                 # second car honks: everyone ready
check(any(pid == 1 and "race start in 5" in m and "all 2 ready" in m for pid, m in chat_out[mark_chat:]), "second honk starts the countdown for the group")
hud_big = []; drawn_mark = len(DRAWN)
for _ in range(110):                                          # 5.5 s: 4,3,2,1, GO
    tick()
    b = [t for t, big in hud_items() if big]
    if b and (not hud_big or hud_big[-1] != b[-1]): hud_big.append(b[-1])
check(hud_big[:6] == ["5", "4", "3", "2", "1", "GO!"], "HUD shows the countdown digits and GO as its big line (%s)" % hud_big[:6])
drawn = list(DRAWN.values())[drawn_mark:]
check(any(d == "x3 GO!" for d in drawn) and any(d.startswith("begin ##kkLapHud") for d in drawn), "HUD draw code runs and draws GO at 3x scale (imgui stub)")
check(any("HUD big text uses scale" in l for l in client_log), "which big-text method worked is logged once")
check(any(t.startswith("race start in 5") for t, _ in hud_items()) or any(n == "kkLap_hud" and "race start in 5" in d for (_, n, d) in events_to_client),
      "'race start in 5' goes to the HUD as well as chat")
check(any(pid == 1 and m.endswith("GO!") for pid, m in chat_out[mark_chat:]) and not any(pid == -1 and m.endswith("GO!") for pid, m in chat_out[mark_chat:]),
      "GO announced to the racers, not to everyone (v0.27)")
check(flashes[-6:] == ["5", "4", "3", "2", "1", "GO!"], "countdown flashed big 5..1 GO (%s)" % flashes[-6:])
check(any("vid 100 GO start" in l for l in client_log[mark_cl:]) and any("vid 101 GO start" in l for l in client_log[mark_cl:]),
      "both parked cars take the GO start")
gos = [json.loads(d) for (pid, n, d) in events_to_client if n == "kkLap_go"]
t_go = gos[-1]["t"]
for _ in range(20): tick()                                    # sit 1 s after GO: must NOT re-arm
for _ in range(100): tick()                                   # 5 s more: every HUD item has run out
check(not any(t in ("5", "4", "3", "2", "1", "GO!") for t, _ in hud_items()), "HUD items expire after their ttl (%s)" % hud_items())
check(sum(1 for pid, m in chat_out[mark_chat:] if pid == 1 and "race start in" in m) == 1, "no second countdown from cars still parked after GO")
velocities[100] = (27.78, 0.0, 0.0); velocities[101] = (27.78, 0.0, 0.0)
# v0.26 (Darren: "first one out starts timer"): nobody's clock runs at GO. Both windows hold 0:00.000 with the GO hint
seat0_vid[0] = 101; tick(); f101 = timer_frames[-1][1]; seat0_vid[0] = 100; tick(); f100 = timer_frames[-1][1]
check(f100 and f101 and texts(f100) == ["x3 0:00.000", "x1 GO - drive out, the first car out starts the clock"] and texts(f101) == texts(f100),
      "v0.26: between GO and the first car out both timer windows hold 0:00.000 with the GO hint (%s)" % texts(f100))
before = len(laps()); E0 = len(events_to_client); ms = len(server_log)
positions[100] = (50, 0, 0); tick(); t1 = clock_ms[0]          # car A drives out: the race clock starts for A AND B, now
first_out = [l for l in server_log[ms:] if re.search(r"race \d+ clock started by Darren#100 leaving the bubble at 100 km/h", l)]
check(infos(E0, 100) == ["timing started"] and infos(E0, 101) == ["timing started"] and len(first_out) == 1,
      "v0.26: the first car out starts the clock: timing started goes to both cars at once, logged with the race number and speed (%s)" % first_out)
for _ in range(13): tick()
positions[101] = (50, 0, 0); tick(); t_b = clock_ms[0]         # car B drives out 700 ms later: nothing new, its clock already runs
check(t_b - t1 == 700 and infos(E0, 101) == ["timing started"] and len(first_out) == 1, "v0.26: the second car out gets no second start")
drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])     # vid 100 lap
t100 = clock_ms[0]
drive(101, [(100, 0, 0)] + [(50, 0, 0)] * 10 + [(0, 0, 0)])     # vid 101 lap, slower (and 700 ms late off the line)
t101 = clock_ms[0]
got = laps()[before:]
check(len(got) == 2, "both cars finish a lap after the GO start")
if len(got) == 2:
    check(got[0]["vid"] == 100 and got[0]["lapMs"] == t100 - t1, "vid 100 lap counted from the instant the first car drove out, not from GO or its own crossing")
    check(got[1]["vid"] == 101 and got[1]["lapMs"] == t101 - t1 and got[1]["lapMs"] == (t101 - t_b) + 700, "vid 101 lap counted from the same instant: its 700 ms late start is in its lap time")
race_tags = [re.search(r"race (\d+)$", m).group(1) for pid, m in chat_out[mark_chat:] if pid == -1 and "  lap " in m and re.search(r"race (\d+)$", m)]
check(len(race_tags) == 2 and race_tags[0] == race_tags[1], "both laps carry the same race number so they compare together (%s)" % race_tags)
# jump start: park, arm, drive off during the countdown
for _ in range(200): tick()                                   # 10 s cooldown after GO
chat("/lap reset"); positions[100] = (0, 0, 0); positions[101] = (300, 300, 0); velocities[100] = (0.0, 0.0, 0.0)
for _ in range(10): tick(); M.onHorn(100)                      # honk: countdown starts
mark_chat = len(chat_out); mark_f = len(flashes); X0 = len(events_to_server)
for _ in range(10): tick()
velocities[100] = (10.0, 0.0, 0.0); positions[100] = (20, 0, 0)   # drives off the line mid-countdown
for _ in range(5): tick()
check(any(pid == 1 and "jumped the start" in m for pid, m in chat_out[mark_chat:]), "driving off during the countdown is announced as a jump start (to the racers, v0.27)")
check(any("jumped the start" in t for t, _ in hud_items()), "the jump start reaches the HUD too")
check(not any(n == "kkLap_jump" for n, _ in events_to_server) and any(n == "kkLap_bubble" and '"inside": false' in d and '"kph"' in d for n, d in events_to_server[X0:])
      and not any(f.startswith("no timing") for f in flashes[mark_f:]),
      "v0.26: the server judged the jump from the bubble leave event (with the speed); the client sent no jump report and showed no 'no timing' hint")
for _ in range(120): tick()                                   # let that countdown finish

# ------------------------------------------------------------------ scenario 9: /lap start moves the line, /lap finish makes it point-to-point
for _ in range(200): tick()                                   # clear the GO cooldown
positions[100] = (5, 0, 0); chat("/lap start"); M.onSurface(100, True, "FL=ASPHALT")
cfg = json.load(open(cfgfile))
check(len(cfg["checkpoints"]) == 2 and cfg["checkpoints"][0]["x"] == 5 and cfg["checkpoints"][1]["x"] == 100,
      "/lap start moves the start line and keeps the other checkpoints")
positions[100] = (200, 0, 0); chat("/lap finish"); M.onSurface(100, False, "FL=GRASS")
check(any("finish must be on tarseal" in m for _, m in chat_out[-2:]), "finish on grass is refused")
chat("/lap finish"); M.onSurface(100, True, "FL=ASPHALT")
cfg = json.load(open(cfgfile))
check(cfg.get("finish", {}).get("x") == 200 and cfg["finish"].get("hx") == 1.0, "finish line stored with its heading")
check(any("finish line painted" in l or "finish line drawn" in l for l in client_log), "client builds a line for the finish too")
velocities[100] = (27.78, 0.0, 0.0); positions[100] = (50, 0, 0); tick()
before = len(laps()); E0 = len(events_to_client)
drive(100, [(5, 0, 0)])                                        # into the start bubble: at the line (v0.26: drag-race shape, honk to go)
race_start([100], cp1=(5, 0, 0))
drive(100, [(50, 0, 0)]); t_s = clock_ms[0]                    # out: the run starts
check(infos(E0, 100) == ["at the line", "GO start", "timing started"], "point-to-point: at the line, GO, the clock starts when the car drives out (%s)" % infos(E0, 100))
drive(100, [(100, 0, 0)] + [(150, 0, 0)] * 55)                 # cp2 split, cruise past minLapMs
drive(100, [(200, 0, 0)]); t_f = clock_ms[0]                   # finish: run ends
got = laps()[before:]
check(len(got) == 1 and got[0]["lapMs"] == t_f - t_s, "point-to-point run timed from driving out of the start to the finish line")
E1 = len(events_to_client)
drive(100, [(150, 0, 0), (50, 0, 0), (5, 0, 0)])               # back into the start bubble: at the line again, no lap, no clock
check(len(laps()) == before + 1 and infos(E1, 100) == ["at the line"], "back in the start after a finish: at the line, counts nothing, nothing starts without a honk")
chat("/lap finish same")
cfg = json.load(open(cfgfile))
check(cfg.get("finish") is None, "/lap finish same puts the finish back on the start line")
chat("/lap reset")

# ------------------------------------------------------------------ scenario 10: music is a broadcast, each client plays the zip file
mark = len(client_log); chat("/lap music kiwi 0.3")
check(any("art/sound/kkLap/kiwi.ogg vol 0.30" in l for l in client_log[mark:]), "/lap music reaches the client with the file and volume (stub audio: playOnce fallback)")
check(any(pid == -1 and "music: kiwi" in m and "on repeat" in m for pid, m in chat_out), "music start announced to everyone, on repeat by default")
mark = len(client_log); chat("/music set.mp3 0.3")
check(any("art/sound/kkLap/set.mp3 vol 0.30" in l for l in client_log[mark:]), "a name with its own extension is used as given (mp3)")
mark = len(client_log); chat("/music set.mp3 3")
check(any("vol 3.00" in l for l in client_log[mark:]), "volume above 1 passes through as a boost")
mark = len(client_log); chat("/music kiwi 0.3 3")            # stub has no stoppable source: timed loop re-fires every 3 s
for _ in range(70): tick()
check(sum(1 for l in client_log[mark:] if "music looped (timed)" in l) == 1, "timed loop re-fires after the given seconds")
mark = len(client_log); chat("/lap music stop")
check(any("music stopped" in l for l in client_log[mark:]), "/lap music stop reaches the client")
# autoplay (v0.21): /music sets the track in config; a fresh car gets it without any command
chat("/music set.mp3 0.4")
check(json.load(open(cfgfile)).get("musicAuto") == "set.mp3", "/music stores the track as the autoplay for everyone")
mark = len(client_log); chat("/set minLapMs 5000")           # any config push: must NOT restart the track
check(not any("music autoplay" in l for l in client_log[mark:]), "a config push does not restart a track that is already playing")
mark = len(client_log)
M.onVehicleSpawned(100)                                       # a (re)spawned own car: already playing, nothing happens
check(not any("music autoplay" in l for l in client_log[mark:]), "respawning while it plays does not restart it")
chat("/music stop")
check(json.load(open(cfgfile)).get("musicAuto") is None, "/music stop clears the autoplay")
# v0.22 bug 2: autoplay waited for an own car, which BeamMP lists only after the server round
# trip, so the first car never got music. Now the config alone starts it, car or no car.
saved_own = own_vids[:]; own_vids[:] = []                      # joined, no car yet
mark = len(client_log); plays = len(AUDIO["plays"])
S.globals().cfg["musicAuto"] = "set.mp3"; S.globals().onGetCfg(1, "")   # server answers the client's config request
check(list(AUDIO["plays"].values())[plays:] == ["art/sound/kkLap/set.mp3"] and any("music autoplay art/sound/kkLap/set.mp3" in l for l in client_log[mark:]),
      "config with musicAuto arriving before any car starts the music on that tick")
chat("/music stop")
# the game refuses the first plays (audio not up yet): retried every 3 s, plays on the 3rd retry, then stops retrying
AUDIO["fail"] = 3; mark = len(client_log); plays = len(AUDIO["plays"])
S.globals().cfg["musicAuto"] = "kiwi"; S.globals().onGetCfg(1, "")   # first try fails here
for _ in range(400): tick()                                    # 20 s
lines = client_log[mark:]
r3 = next((i for i, l in enumerate(lines) if "music autoplay retry 3 of 10" in l), None)
check(r3 is not None and any("music playing art/sound/kkLap/kiwi.ogg" in l for l in lines[r3:r3 + 3]) and
      not any("retry 4" in l for l in lines) and list(AUDIO["plays"].values())[plays:] == ["art/sound/kkLap/kiwi.ogg"],
      "a refused autoplay is retried and plays on the 3rd retry, then retrying stops")
chat("/music stop")
AUDIO["fail"] = 1000; mark = len(client_log)                   # audio never works: 10 retries, then quiet until the next config
S.globals().cfg["musicAuto"] = "kiwi"; S.globals().onGetCfg(1, "")
for _ in range(800): tick()                                    # 40 s
check(sum(1 for l in client_log[mark:] if "music autoplay retry" in l) == 10 and any("gave up after 10" in l for l in client_log[mark:]),
      "autoplay gives up after 10 retries")
AUDIO["fail"] = 0; chat("/music stop"); own_vids[:] = saved_own

# ------------------------------------------------------------------ scenario 11: commands without the lap word
mark = len(chat_out)
check(chat("/times") == 1 and len(chat_out) > mark, "/times works like /lap times (answers, and swallows the message)")
check(chat("/foo bar") is None and chat("hello /lap") is None, "other slash commands and plain chat are left alone")
mark = len(chat_out); chat("/help")
check(any("/rules" in m for _, m in chat_out[mark:]), "/help is ours")
mark = len(chat_out); chat("/set minLapMs 4000")
check(any("minLapMs = 4000" in m for _, m in chat_out[mark:]), "/set carries its arguments through")
chat("/set minLapMs 5000")
mark = len(chat_out); chat("/dj Voice channel #kiwi-dj, link in #announcements"); chat("/dj")
check(sum(1 for pid, m in chat_out[mark:] if pid == -1 and "[DJ] Voice channel #kiwi-dj" in m) == 2, "/dj sets and repeats the Discord pointer to everyone")

# ------------------------------------------------------------------ scenario 12: rules window and admin gate
mark = len(client_log)
rs = M.rulesState
chat("/rules")
check(rs.open == True and len(list(rs.lines.values())) >= 5 and rs.admin is not None, "/rules opens the window with the rules, and admin lines for an admin (nobody set yet = everyone)")
for _ in range(3): tick()                                     # drawRules runs: no imgui here, must not crash
chat("/rules")
check(rs.open == False, "/rules again closes it")
chat("/admin add Darren")
check(json.load(open(cfgfile))["admins"] == ["Darren"], "/admin add locks the admin list to that name")
mark = len(chat_out)
check(chat_as(2, "Bob", "/mark") == 1 and any(pid == 2 and "admin only" in m for pid, m in chat_out[mark:]), "non-admin cannot mark")
check(chat_as(2, "Bob", "/times") == 1 and not any(pid == 2 and "admin only" in m for pid, m in chat_out[mark+1:]), "non-admin can read the board")
check(chat_as(2, "Bob", "/dj") == 1 and chat_as(2, "Bob", "/dj hack") == 1 and any(pid == 2 and m == "[LAP] admin only" for pid, m in chat_out[mark:]), "non-admin can ask where the DJ is but not change it")
chat("/rules add No ramming on the start straight")
check(any("No ramming" in l for l in json.load(open(cfgfile))["rules"]), "/rules add stores a rule")
S.globals()[s_handlers["onPlayerJoin"]](1)
check(rs.open == True and any("No ramming" in l for l in rs.lines.values()), "joining opens the rules window with the new rule")
chat("/rules")

# ------------------------------------------------------------------ scenario 8: vehicle-side file, in its own VM, reports the horn edge once
check(any("loadModulesInDirectory('lua/vehicle/extensions/kkLap')" in c for _, c in ve_cmds), "GE side loads kkLapVE into the cars")
V = luajit21.LuaRuntime(unpack_returned_tuples=True)         # same LuaJIT family as the game's vehicle VM
ge_calls = []
V.globals().electrics = V.table(values=V.table(horn=0))
V.globals().obj = V.table(getID=lambda self: 100, queueGameEngineLua=lambda self, s: ge_calls.append(s))
VE = V.execute((ROOT / "Resources/Client/kkLap/lua/vehicle/extensions/kkLap/kkLapVE.lua").read_text())
VE.updateGFX(0.05); V.globals().electrics["values"]["horn"] = 1; VE.updateGFX(0.05); VE.updateGFX(0.05)
V.globals().electrics["values"]["horn"] = 0; VE.updateGFX(0.05); V.globals().electrics["values"]["horn"] = True; VE.updateGFX(0.05)
check(ge_calls == ["kkLap.onHorn(100)", "kkLap.onHorn(100)"], "horn held down reports once per press, number or boolean (%s)" % ge_calls)
# surface report: two wheels on asphalt (10) with rubber (4) in either slot, one on grass (20), one in the air (-1)
mats = V.table_from({10: V.table(name="ASPHALT"), 20: V.table(name="GRASS"), 4: V.table(name="RUBBER")})
V.globals().particles = V.table(getMaterialsParticlesTable=lambda: mats, getMaterialByID=lambda m, i: m[i])
V.globals().wheels = V.table(wheels=V.table_from({0: V.table(name="FL", contactMaterialID1=10, contactMaterialID2=4),
                                                  1: V.table(name="FR", contactMaterialID1=4,  contactMaterialID2=10),
                                                  2: V.table(name="RL", contactMaterialID1=20, contactMaterialID2=4),
                                                  3: V.table(name="RR", contactMaterialID1=-1, contactMaterialID2=-1)}))
ge_calls.clear(); VE.reportSurface()
check(ge_calls == ['kkLap.onSurface(100, false, "FL=ASPHALT,FR=ASPHALT,RL=GRASS,RR=NONE")'], "surface report names every wheel and says not sealed with one wheel on grass (%s)" % ge_calls)
V.globals().wheels["wheels"][2]["contactMaterialID1"] = 10
ge_calls.clear(); VE.reportSurface()
check(ge_calls == ['kkLap.onSurface(100, true, "FL=ASPHALT,FR=ASPHALT,RL=ASPHALT,RR=NONE")'], "all touching wheels on asphalt = sealed, airborne wheel ignored")
ge_calls.clear(); VE.onReset()
check(ge_calls == ["kkLap.onVehReset(100)"], "vehicle reset is reported to the game side")

# ------------------------------------------------------------------ scenario 6: /lap markers off reaches every client
mark = len(server_log)
chat("/lap markers off")
check(any("markers=false" in l for l in server_log[mark:]), "/lap markers off is pushed to the client config")
check(any("markers false" in m for _, m in chat_out), "chat confirms markers off")

# ------------------------------------------------------------------ scenario 13: the start bubble never jams (v0.22 bug 1)
# Live 30/10: two cars in the bubble, the second never honked or was respawned, the race
# could never start. A car that leaves (prune, drive out, deletion) is re-counted at once;
# a second honk from a ready car starts without the rest.
chat("/lap markers on")
def bubble_setup():
    own_vids[:] = [100, 101]; seat0_vid[0] = 100
    for _ in range(220): tick()                               # GO cooldown and any countdown long gone
    positions[100] = (5, 0, 0); positions[101] = (7, 0, 0)     # both parked in the start bubble (start at x=5, r=8)
    velocities[100] = (0.0, 0.0, 0.0); velocities[101] = (0.0, 0.0, 0.0)
    chat("/lap reset")
    for _ in range(10): tick()
    M.onHorn(100)
    return len(chat_out)
def countdown_since(mark): return [m for pid, m in chat_out[mark:] if pid == 1 and "race start in" in m]   # v0.27: said to the racers (pid 1), never to -1
def bubble_keys(): return set(S.globals().bubble.keys())

# (1) car 2 disappears from the own map: the 1 s sweep drops it, tells the server, countdown starts
mark = bubble_setup()
check(any("ready  (1 of 2" in m for _, m in chat_out[mark - 2:]) and not countdown_since(mark), "two cars in the bubble, one honk: waiting for the second")
mark_e = len(events_to_server)
own_vids[:] = [100]
for _ in range(25): tick()
check(any(n == "kkLap_bubble" and '"vid": 101' in d and '"inside": false' in d for n, d in events_to_server[mark_e:]), "the sweep tells the server car 2 left the bubble")
cd = countdown_since(mark)
check(len(cd) == 1 and "last car left" in cd[0], "car 2 pruned: the countdown starts for the ready car (%s)" % cd)
for _ in range(120): tick()

# (2) car 2 sits there and never honks: car 1 honks twice
mark = bubble_setup()
M.onHorn(100)
check(any(pid == 1 and "honked twice: starting without the rest" in m for pid, m in chat_out[mark:]) and len(countdown_since(mark)) == 1,
      "a second honk from a ready car starts the countdown without the car that never honked")
check(any("honked twice" in t for t, _ in hud_items()), "the honk-twice line reaches the HUD")
for _ in range(120): tick()

# (3) car 2 drives out of the bubble
mark = bubble_setup()
positions[101] = (300, 300, 0)
for _ in range(3): tick()
cd = countdown_since(mark)
check(len(cd) == 1 and "last car left" in cd[0], "car 2 drives out: the countdown starts for the ready car")
for _ in range(120): tick()
positions[101] = (7, 0, 0)

# (4) car 2 is deleted (Ctrl+R / removed): be:getObjectByID gives nil, BeamMP still lists it
mark = bubble_setup()
check("1:101" in bubble_keys(), "server has car 2 in the bubble before it is deleted")
mark_e = len(events_to_server)
del positions[101]                                            # stub be:getObjectByID(101) now returns nil
for _ in range(25): tick()
check(any(n == "kkLap_bubble" and '"vid": 101' in d and '"inside": false' in d for n, d in events_to_server[mark_e:]) and "1:101" not in bubble_keys(),
      "deleted car: the sweep sends inside=false and the server drops it")
check(len(countdown_since(mark)) == 1, "deleted car: the countdown starts for the ready car")
for _ in range(120): tick()
positions[101] = (7, 0, 0)

# (5) the game's onVehicleDestroyed hook does it at once, no sweep needed
mark = bubble_setup()
mark_e = len(events_to_server)
M.onVehicleDestroyed(101)
check(any(n == "kkLap_bubble" and '"vid": 101' in d and '"inside": false' in d for n, d in events_to_server[mark_e:]) and len(countdown_since(mark)) == 1,
      "onVehicleDestroyed tells the server straight away and the countdown starts")
for _ in range(120): tick()
check(not dropped, "no client event was dropped by the server (after the v0.22 scenarios)")

# ------------------------------------------------------------------ scenario 14: the race timer window (v0.23)
# Darren 30/10: "the timer, or how far I am ahead of last lap, is small and hard to read and
# distracting; place it like single-player time trials". One imgui window top centre: big clock,
# medium red/green gap for 3 s, small best line. No toasts for splits or laps.
own_vids[:] = [100, 101]; seat0_vid[0] = 100
positions[100] = (50, 0, 0); positions[101] = (300, 300, 0)
velocities[100] = (27.78, 0.0, 0.0); velocities[101] = (27.78, 0.0, 0.0)
for _ in range(220): tick()
# v0.27: one lap puts a driver on the board, and the board's best sectors are the reference for the split
# gaps (v0.24). So the "no reference" lap below needs the board wiped: /lap clear, the same two points again.
chat("/lap clear")
positions[100] = (5, 0, 0); chat("/lap mark"); M.onSurface(100, True, "FL=ASPHALT")
positions[100] = (100, 0, 0); chat("/lap mark 10"); M.onSurface(100, True, "FL=ASPHALT")
positions[100] = (50, 0, 0)
for _ in range(5): tick()
check(all(f is None for _, f in timer_frames[-5:]), "timer: no window while no own car is timing")
hud_seen.clear(); mark_t = len(toasts); mark_f = len(flashes)
positions[100] = (5, 0, 0); tick()                            # into the start bubble: at the line (0:00.000, checked in the v0.26 scenario)
race_start([100], cp1=(5, 0, 0))                              # honk, countdown, GO
positions[100] = (50, 0, 0); tick(); t0 = clock_ms[0]         # out: the clock starts
for _ in range(19): tick()
fr = tframes(t0, clock_ms[0])
check(fr and all(f and RUN.match(texts(f)[0]) and len(f) == 1 for f in fr), "timer: running clock big, alone, from the frame after timing starts")
# lap A: first sector 1 s, no reference yet after /lap reset: no gap line at all
positions[100] = (100, 0, 0); tick(); t_sp = clock_ms[0]
spA = json.loads([d for (p, n, d) in events_to_client if n == "kkLap_split"][-1])
positions[100] = (50, 0, 0)
for _ in range(100): tick()
check(spA.get("deltaMs") is None and not any(f and any(is_gap(d) for d in texts(f)) for f in tframes(t_sp, clock_ms[0])),
      "timer: first lap after /lap reset has no reference, so no gap line")
check(not any(f and any(d.startswith("x1 best") for d in texts(f)) for f in tframes(t0, clock_ms[0])), "timer: no best line before a lap is set")
positions[100] = (5, 0, 0); tick(); t_lap = clock_ms[0]          # lap A
lp = laps()[-1]
# lap B: straight from the line into cp2 in one frame, a faster first sector
positions[100] = (100, 0, 0); tick(); t_sp2 = clock_ms[0]
spB = json.loads([d for (p, n, d) in events_to_client if n == "kkLap_split"][-1])
positions[100] = (50, 0, 0)
for _ in range(110): tick()
hold, back = tframes(t_lap, t_lap + 3800), tframes(t_lap + 4200, clock_ms[0])
check(lp["vid"] == 100 and hold and all(f and texts(f)[0] == "x3 LAP  " + fmt(lp["lapMs"]) and f[0][1] == "1.00,0.90,0.30" for f in hold),
      "timer: after a lap 'LAP  %s' holds big for 4 s" % fmt(lp["lapMs"]))
check(back and all(f and RUN.match(texts(f)[0]) for f in back), "timer: then the running clock is back")
check(spB.get("deltaMs") is not None and spB["deltaMs"] < 0 and all(f and gap_line(spB["deltaMs"]) in f for f in tframes(t_sp2, t_sp2 + 2800)),
      "timer: a faster sector shows green with a minus (%s)" % (gap_line(spB["deltaMs"]) if spB.get("deltaMs") is not None else None,))
check(all(f and texts(f)[-1] == "x1 best " + fmt(lp["bestMs"]) + "   lap 1" for f in tframes(t_lap, clock_ms[0])),
      "timer: the small best line appears once a lap is set and stays, with the lap count (v0.26)")
# v0.26 (Darren: "it doesn't show stage time or speed, u missing all the cool info now"): sector line, gap beside it, split under it
sec_a = "x2 S2  %.1f s   100 km/h" % (lp["sectors"][-1] / 1000)
fr_a = tframes(t_lap, t_sp2)                                  # until lap B's first split replaces the info line
check(fr_a and all(f and texts(f)[1] == sec_a for f in fr_a), "v0.26: after a lap the info line shows the last sector and the speed over the line (%s)" % sec_a)
sec_b = "x2 S1  %.1f s   100 km/h" % (spB["sectorMs"] / 1000)
fr_b = tframes(t_sp2, t_sp2 + 2800)
check(fr_b and all(f and texts(f)[1:5] == [sec_b, "sameline", gap_line(spB["deltaMs"])[0], "x1 split " + fmt(spB["splitMs"])] for f in fr_b),
      "v0.26: after a split the timer window shows '%s', the gap on the same line (SameLine) and 'split m:ss.mmm' under it" % sec_b)
fr_b2 = tframes(t_sp2 + 3100, t_sp2 + 4800)
check(fr_b2 and all(f and sec_b in texts(f) and not any(is_gap(d) for d in texts(f)) for f in fr_b2), "v0.26: the sector line stays 5 s, the gap only 3 s")
# lap C: a slow first sector again: red plus, 3 s, then gone
positions[100] = (5, 0, 0); tick(); lpB = laps()[-1]
positions[100] = (50, 0, 0)
for _ in range(20): tick()
positions[100] = (100, 0, 0); tick(); t_sp3 = clock_ms[0]
spC = json.loads([d for (p, n, d) in events_to_client if n == "kkLap_split"][-1])
positions[100] = (50, 0, 0)
for _ in range(100): tick()
if spC.get("deltaMs") is not None and spC["deltaMs"] > 0:
    g = gap_line(spC["deltaMs"])
    on, off = tframes(t_sp3, t_sp3 + 2800), tframes(t_sp3 + 3100, clock_ms[0])
    check(on and all(f and g in f for f in on), "timer: a slower sector shows red with a plus for 3 s %s" % (g,))
    check(off and all(f and not any(is_gap(d) for d in texts(f)) for f in off), "timer: then the gap line is gone")
else:
    check(False, "timer: lap C sector should be slower than lap B's (%s)" % spC.get("deltaMs"))
check(lpB["laps"] == 2 and texts(timer_frames[-1][1])[-1] == "x1 best " + fmt(lpB["bestMs"]) + "   lap 2", "timer: best line follows the server's best and lap count for this car")
check(not any(t.startswith("S1") or "lap " in t for t in toasts[mark_t:]) and not any(f.startswith("S1") or f.startswith("LAP") for f in flashes[mark_f:])
      and not any(t.startswith("S1") or t.startswith("LAP") or "last sector" in t for t in hud_seen),
      "timer: no toast, flash or HUD-queue item for splits or laps (%s)" % sorted(hud_seen))
# two own cars timing: the window follows the car the camera is on (be:getPlayerVehicle(0))
positions[101] = (50, 0, 0); tick(); positions[101] = (5, 0, 0); tick()      # 101 into the bubble: at the line
race_start([101], cp1=(5, 0, 0)); positions[101] = (50, 0, 0); tick()      # honk, GO, out: its clock runs (v0.26)
seat0_vid[0] = 101; tick()
f101 = timer_frames[-1][1]
seat0_vid[0] = 100; tick()
f100 = timer_frames[-1][1]
check(M.timerVid() == 100 and f101 and not any(d.startswith("x1 best") for d in texts(f101)) and f100 and texts(f100)[-1].startswith("x1 best"),
      "timer: two own cars timing, the window shows the one the camera is on (101 no best yet, 100 has one)")
seat0_vid[0] = None; tick()
check(M.timerVid() == 100, "timer: no player vehicle, the first own timing car is shown")
seat0_vid[0] = 100
# an imgui error on the timer window: hud.broken, fall back to the v0.22 path, logged once
mark_c = len(client_log)
C.globals().ui_imgui["failBegin"] = "##kkLapTimer"
for _ in range(6): tick()
check(HUD["broken"] and sum("lap timer path: Race Realtime Display app + toasts" in l for l in client_log[mark_c:]) == 1
      and any("HUD timer failed to draw" in l for l in client_log[mark_c:]), "timer: an imgui error falls back to the old path, logged once")
check(re.match(r"^\d+:\d\d\.\d{3}$", realtime[0] or ""), "timer fallback: the running clock goes to the Race Realtime Display app (%s)" % realtime[0])
mark_t = len(toasts)
positions[101] = (100, 0, 0); tick(); positions[101] = (50, 0, 0); tick()        # 101 is mid-lap: its split
check(any(t.startswith("S1") for t in toasts[mark_t:]), "timer fallback: the sector toast comes back so nothing is lost")
C.globals().ui_imgui["failBegin"] = None; HUD["broken"] = False
mark_c = len(client_log)
tick(); tick()
check(realtime[0] == "" and any("lap timer path: imgui window" in l for l in client_log[mark_c:]) and texts(timer_frames[-1][1])[:1] and RUN.match(texts(timer_frames[-1][1])[0]),
      "timer: HUD back, the UI-app clock is cleared and the window draws again")
# /hud off: the window goes, the old path takes over
chat("/hud off"); chat("/lap reset")                       # a config resend clears the client state; reset the server side too
check(any("on-screen HUD false" in m for _, m in chat_out[-3:]), "/hud off accepted")
positions[100] = (5, 0, 0)
for _ in range(3): tick()
check(realtime[0] == "0:00.000", "timer fallback: at the line the UI app shows 0:00.000 (v0.26) (%s)" % realtime[0])
race_start([100], cp1=(5, 0, 0)); positions[100] = (50, 0, 0)
for _ in range(10): tick()
check(all(f is None for _, f in timer_frames[-10:]) and re.match(r"^\d+:\d\d\.\d{3}$", realtime[0] or "") and realtime[0] != "0:00.000",
      "timer: /hud off hides the window and the running clock goes to the UI app instead")
chat("/hud on")
for _ in range(3): tick()

# ================================================================== v0.24: the review findings (30/10)
# A reviewer traced these against v0.22 with a probe harness; each case below failed on v0.23
# and passes on v0.24. Every scenario is guarded: a throw in the plugin is a FAIL, not a crash.
G = S.globals()
def guard(what, fn):
    try: fn()
    except Exception as e: check(False, "%s: raised %s" % (what, str(e).splitlines()[0][:120]))
def fresh_track(points, own=(100,), park=(-30, 0, 0)):
    """wait out any countdown, /lap clear, mark the points with car 100, park it outside everything"""
    own_vids[:] = list(own); seat0_vid[0] = own[0]
    for v in list(positions):
        if v not in own and v != 100: del positions[v]
    velocities[100] = (27.78, 0.0, 0.0)
    C.globals().ui_imgui["failText"] = False; HUD["broken"] = False
    chat("/lap clear")                                            # first: a broken track from the scenario before must not throw here
    for _ in range(220): tick()
    chat("/lap clear")
    for p in points:
        positions[100] = p; chat("/lap mark"); M.onSurface(100, True, "FL=ASPHALT")
    positions[100] = park
    for _ in range(3): tick()
def board(): return json.load(open("Resources/Server/kkLap/times.json"))

# ---- 1: a config push mid-lap (/hud on) must not cost the lap (cfg.trackRev)
def s_v24_1():
    fresh_track([(0, 0, 0), (100, 0, 0), (100, 100, 0)], own=(100, 101))
    positions[101] = (2, 0, 0); velocities[101] = (0.0, 0.0, 0.0)   # a second car parked in the start bubble
    for _ in range(3): tick()
    L0 = len(laps()); E0 = len(events_to_client)
    drive(100, [(0, 0, 0)], step_ms=1000); race_start([100, 101])                 # both in the bubble: honk, GO (v0.26)
    drive(100, [(50, 0, 0), (100, 0, 0), (100, 50, 0)], step_ms=1000)            # 100 out (the clock starts for both), cp2: now heading for cp3
    rev = G.cfg["trackRev"]
    chat("/hud on")                                               # any admin command that pushes the config
    check(G.cfg["trackRev"] == rev and "1:101" in bubble_keys(), "v0.24 #1: /hud on pushes the config but keeps the track revision and the bubble roster")
    for _ in range(3): tick()
    drive(100, [(100, 100, 0), (50, 50, 0), (-30, -30, 0), (0, 0, 0)], step_ms=1000)   # cp3, then the line
    got = laps()[L0:]
    check(len(got) == 1 and len(got[0]["sectors"]) == 3 and not any(m.startswith("wrong cp") for m in infos(E0, 100)),
          "v0.24 #1: car past cp2, admin /hud on, the next line crossing still counts a normal 3-sector lap (%s)" % [(l["lapMs"], l["sectors"]) for l in got])
    mark = len(events_to_client); chat("/lap reset")
    check(rev is not None and G.cfg["trackRev"] == rev + 1 and json.load(open(cfgfile)).get("trackRev") == rev + 1, "v0.24 #1: /reset moves trackRev, saved in config.json")
guard("v0.24 #1", s_v24_1)

# ---- 2: one checkpoint only: the car must leave the sphere before it can cross again
def s_v24_2():
    fresh_track([(0, 0, 0)])
    L0 = len(laps()); X0 = len(events_to_server)
    def passline():
        for x in [-20, -6, -3, 0, 3, 6, 20]:
            positions[100] = (x, 0, 0); tick(ms=100)
    positions[100] = (0, 0, 0); tick(ms=100)                     # into the one sphere: at the line (v0.26)
    race_start([100])                                            # honk, GO; the car sits inside the target sphere
    for x in [3, 6, 20]: positions[100] = (x, 0, 0); tick(ms=100)   # out: the clock starts
    for _ in range(10): positions[100] = (200, 0, 0); tick(ms=1000)
    positions[100] = (-30, 0, 0); tick(ms=1000)
    passline()                                                   # through it again: the lap
    c = [(x["cp"], x.get("restart")) for x in crosses(X0, 100)]
    check(c == [(1, True), (1, None)], "v0.24 #2: 1-checkpoint track: one 'at the line' cross when the car parks in it, then exactly one crossing when it passes through again (%s)" % c)
    check(len(laps()) == L0 + 1, "v0.24 #2: and that pass is a lap")
guard("v0.24 #2", s_v24_2)

# ---- 6: /set and /race take only sane numbers; /race stop
def s_v24_6():
    fresh_track([(0, 0, 0), (100, 0, 0)])
    mark = len(chat_out)
    try: chat("/set countdown 1e999")
    except Exception as e: check(False, "v0.24 #6: /set countdown 1e999 raised " + str(e)[:80])
    check(G.cfg["countdown"] == 5 and said(mark, "countdown must be 1..60"), "v0.24 #6: /set countdown 1e999 is refused with the range")
    chat("/set countdown 0.5"); chat("/set markerAlpha 300"); chat("/set trackRev 7")
    check(G.cfg["countdown"] == 5 and G.cfg["markerAlpha"] == 40 and said(mark, "unknown numeric option trackRev"),
          "v0.24 #6: out-of-range and unlisted keys refused (countdown 0.5, markerAlpha 300, trackRev)")
    chat("/set countdown 3.9")
    check(G.cfg["countdown"] == 3 and said(mark, "countdown = 3"), "v0.24 #6: an integer key is rounded down (3.9 -> 3)")
    chat("/set countdown 5"); mark = len(chat_out)
    try: chat("/race")
    except Exception as e: check(False, "v0.24 #6: /race raised " + str(e)[:80])
    for _ in range(120): tick()
    check(said(mark, "race start in 5") and said(mark, "GO!"), "v0.24 #6: after the refused /set the countdown still runs to GO")
    for _ in range(220): tick()
    mark = len(chat_out); chat("/race 999")
    check(said(mark, "race start in 60"), "v0.24 #6: /race seconds clamped to 60")
    for _ in range(40): tick()
    positions[100] = (0, 0, 0); velocities[100] = (0.0, 0.0, 0.0)
    for _ in range(3): tick()
    mark = len(chat_out); chat("/race stop")
    check(said(mark, "countdown cancelled", -1) and "kkLap_tick" not in timers, "v0.24 #6: /race stop cancels the countdown and its timer")
    for _ in range(60): tick()
    check(not said(mark, "GO!"), "v0.24 #6: no GO after /race stop")
    G.cfg["countdown"] = 5                                        # /race 999 left it at 60
    X0 = len(events_to_server); M.onHorn(100)
    check(any(n == "kkLap_arm" for n, _ in events_to_server[X0:]) and said(mark, "race start in 5"),
          "v0.24 #6: after /race stop a honk starts a new countdown (the client is not stuck counting)")
    for _ in range(130): tick()
    velocities[100] = (27.78, 0.0, 0.0)
guard("v0.24 #6", s_v24_6)

# ---- 7: /finish with no start; /undo emptying the list; a client given a finish and no start
def s_v24_7():
    fresh_track([])
    positions[100] = (200, 0, 0); mark = len(chat_out)
    chat("/finish"); M.onSurface(100, True, "FL=ASPHALT")
    check(json.load(open(cfgfile)).get("finish") is None and said(mark, "mark the start first"), "v0.24 #7: /finish with no start is refused")
    for _ in range(3): tick()
    positions[100] = (0, 0, 0); chat("/start"); M.onSurface(100, True, "FL=ASPHALT")
    positions[100] = (200, 0, 0); chat("/finish"); M.onSurface(100, True, "FL=ASPHALT")
    check(json.load(open(cfgfile)).get("finish") is not None, "v0.24 #7: start then finish is accepted")
    chat("/undo")
    c = json.load(open(cfgfile))
    check(not c["checkpoints"] and c.get("finish") is None, "v0.24 #7: /undo that removes the start removes the finish too")
    G.cfg["finish"] = S.table(x=200, y=0, z=0, r=8)               # a hand-edited config.json: finish, no start
    G.onGetCfg(1, "")
    err = None
    try:
        for _ in range(5): tick()
    except Exception as e: err = str(e).splitlines()[0][:100]
    check(err is None, "v0.24 #7: a client given a finish and no start does not throw every frame (%s)" % err)
    chat("/lap clear")
guard("v0.24 #7", s_v24_7)

# ---- 5: a teleport with no reset event where the car STAYS
def s_v24_5():
    fresh_track([(0, 0, 0), (100, 0, 0)])
    drive(100, [(0, 0, 0)], step_ms=500); race_start([100]); drive(100, [(50, 0, 0)], step_ms=500)   # at the line, GO, out: lap running
    mark = len(chat_out)
    positions[100] = (600, 600, 0)
    for _ in range(25): tick()                                    # 1.25 s, never moves back
    check(said(mark, "will not count") and said(mark, "in one frame"), "v0.24 #5: 600 m jump, the car stays there: the lap is invalidated within ~1 s")
    for _ in range(25): tick()
    check(len(said(mark, "will not count")) == 1, "v0.24 #5: judged once, not again every second")
guard("v0.24 #5", s_v24_5)

# ---- 4 and 3: the board keeps the BEST lap's sectors and speed; an invalidated lap sets no best sector
def s_v24_4_3():
    fresh_track([(0, 0, 0), (100, 0, 0)])
    L0 = len(laps())
    drive(100, [(0, 0, 0)], step_ms=2000); race_start([100])                                 # at the line, honk, GO (v0.26)
    drive(100, [(50, 0, 0), (50, 0, 0), (100, 0, 0), (50, 50, 0), (0, 0, 0)], step_ms=2000)  # out (clock starts), lap 1: 8 s, [4, 4], 100 km/h
    velocities[100] = (10.0, 0.0, 0.0)                                                    # lap 2 crosses the line at 36 km/h
    drive(100, [(50, 0, 0), (100, 0, 0), (50, 50, 0), (50, 50, 0), (50, 50, 0), (-30, 0, 0), (0, 0, 0)], step_ms=2000)   # 14 s
    got = [(l["lapMs"], l["sectors"]) for l in laps()[L0:]]
    e = board().get("id:42", {})
    check(got == [(8000, [4000, 4000]), (14000, [4000, 10000])] and e.get("bestMs") == 8000 and e.get("sectors") == [4000, 4000]
          and e.get("laps") == 2 and e.get("kph") == 100,
          "v0.24 #4: lap1 8 s, lap2 14 s: board bestMs 8000 with lap 1's sectors and speed, laps 2 (%s)" % {k: e.get(k) for k in ("bestMs", "sectors", "laps", "kph")})
    velocities[100] = (27.78, 0.0, 0.0)
    # lap 3: a position reset lands the car in cp2 (fast sector 1), then a fast last sector: invalidated
    mark = len(chat_out)
    positions[100] = (30, 0, 0); tick(ms=500)
    positions[100] = (95, 0, 0); M.onVehReset(100); tick(ms=600)
    drive(100, [(50, 50, 0), (0, 0, 0)], step_ms=1000)
    check(said(mark, "lap will not count"), "v0.24 #3: (setup) lap 3 is invalidated by a position reset")
    # lap 4: the same 4 s sectors as lap 1: the deltas must be against lap 1, not the invalidated lap
    E0 = len(events_to_client)
    drive(100, [(50, 0, 0), (100, 0, 0), (50, 50, 0), (0, 0, 0)], step_ms=2000)
    sp = [json.loads(d) for p, n, d in events_to_client[E0:] if n == "kkLap_split"]
    lp = laps()[-1]
    check(sp and sp[0].get("deltaMs") == 0 and board()["id:42"].get("bestSectors") == [4000, 4000],
          "v0.24 #3: an invalidated lap leaves bestSectors untouched (lap 4 split delta %s, board bestSectors %s)" % (sp and sp[0].get("deltaMs"), board()["id:42"].get("bestSectors")))
    check(lp["laps"] == 3 and lp["lapMs"] == 8000, "v0.24 #3: (setup) lap 4 counts as the run's third lap")
guard("v0.24 #4/#3", s_v24_4_3)

# ---- 12: a car flying through the start bubble at GO gets an ordinary line start, not the GO instant
def s_v24_12():
    fresh_track([(0, 0, 0), (100, 0, 0)], own=(100, 101))
    positions[101] = (2, 0, 0); velocities[101] = (0.0, 0.0, 0.0)
    for _ in range(3): tick()
    mark = len(chat_out); M.onHorn(101)
    check(said(mark, "race start in"), "v0.24 #12: (setup) car 101 alone in the bubble honks, countdown")
    held = []; real_go = c_handlers["kkLap_go"]
    c_handlers["kkLap_go"] = lambda d: held.append(d)            # kkLap_go still on the wire (network delay)
    E0 = len(events_to_client)
    for _ in range(120):
        tick()
        if held: break
    c_handlers["kkLap_go"] = real_go
    for _ in range(20): tick()                                   # 1 s after the server's GO, car 100 arrives at speed
    positions[100] = (0, 0, 0); tick(); t_entry = clock_ms[0]
    for d in held: real_go(d)                                    # ... and only now does the client hear GO
    i100, i101 = infos(E0, 100), infos(E0, 101)
    check(held and i101 == ["GO start"], "v0.24 #12: the car parked in the bubble at GO is named a starter by the server, GO heard late or not (%s)" % i101)
    check(i100 == ["at the line"], "v0.26: the car that arrived after GO is not a starter: at the line, no GO start, no timing (%s)" % i100)
    L0 = len(laps()); X0 = len(events_to_server)
    drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])
    check(len(laps()) == L0 and infos(E0, 100) == ["at the line", "at the line"] and all(x.get("restart") for x in crosses(X0, 100)),
          "v0.26: its drive round without a honk is no lap: no crossing sent but the 'at the line' one, back at the line (%s)" % infos(E0, 100))
    E1 = len(events_to_client)
    velocities[101] = (27.78, 0.0, 0.0); positions[101] = (50, 0, 0); tick()
    check(infos(E1, 101) == ["timing started"] and infos(E1, 100) == [], "v0.26: the starter driving out starts its own race clock only")
guard("v0.24 #12", s_v24_12)

# ---- 9: leaving the level stops the music for good (no autoplay retry 3 s later)
def s_v24_9():
    chat("/music set.mp3 0.4")
    for _ in range(10): tick()
    M.onClientEndMission()
    plays = len(AUDIO["plays"]); mark = len(client_log)
    for _ in range(200): tick()                                  # 10 s in the menu
    check(len(AUDIO["plays"]) == plays and not any("music autoplay" in l for l in client_log[mark:]),
          "v0.24 #9: end mission, 10 s later the music has not been started again")
    chat("/music stop")
guard("v0.24 #9", s_v24_9)

# ---- 10: one /music = one play; a source that half-worked is deleted
def s_v24_10():
    plays = len(AUDIO["plays"]); chat("/music kiwi 0.3")
    check(len(AUDIO["plays"]) - plays == 1, "v0.24 #10: /music kiwi (on repeat) = exactly one play call (%d)" % (len(AUDIO["plays"]) - plays))
    plays = len(AUDIO["plays"]); chat("/music kiwi 0.5 once")
    check(len(AUDIO["plays"]) - plays == 1, "v0.24 #10: /music kiwi 0.5 once = exactly one play call")
    chat("/music stop")
    AUDIO["srcId"] = 77; dels = len(AUDIO["deleted"])
    chat("/music kiwi 0.3")
    check(77 in list(AUDIO["deleted"].values())[dels:], "v0.24 #10: createSource gave id 77 but the object lookup failed: deleteSource(77)")
    AUDIO["srcId"] = None; chat("/music stop")
guard("v0.24 #10", s_v24_10)

# ---- 8: /rules add and clear are admin only; a % in a rule or on the HUD is drawn as it is
def s_v24_8():
    mark = len(chat_out); n = len(G.cfg["rules"])
    chat_as(2, "Bob", "/rules add %s%s%n"); chat_as(2, "Bob", "/rules clear")
    check(len(G.cfg["rules"]) == n and len(said(mark, "admin only", 2)) == 2, "v0.24 #8: a non-admin cannot /rules add or /rules clear")
    chat_as(2, "Bob", "/rules")
    check(rs.open == True, "v0.24 #8: a non-admin can still open the window")
    chat("/rules add Use 100% throttle, no %s tricks")
    G.onPlayerJoin(1)                                             # opens the window with the new rule
    m0 = len(DRAWN)
    for _ in range(3): tick()
    drawn = list(DRAWN.values())[m0:]
    check(rs.open == True and "w - Use 100% throttle, no %s tricks" in drawn, "v0.24 #8: a rule containing %s and % is drawn as written (printf-style stub)")
    chat("/rules")
    deliver_to_client("kkLap_hud", json.dumps({"msg": "p%s ready 100%"}))
    m0 = len(DRAWN); tick()
    check(not HUD["broken"] and "x1 p%s ready 100%" in list(DRAWN.values())[m0:], "v0.24 #8: a HUD line with % in it is drawn as written, HUD not broken")
    G.cfg["rules"] = S.table_from([r for r in G.cfg["rules"].values() if "%" not in r])
guard("v0.24 #8", s_v24_8)

# ---- 13: an imgui error between Begin and End still Ends the window
def s_v24_13():
    for _ in range(120): tick()                                   # old HUD items gone
    G.onPlayerJoin(1)                                             # rules window open
    deliver_to_client("kkLap_hud", json.dumps({"msg": "ready line"}))
    C.globals().ui_imgui["failText"] = True
    m0 = len(DRAWN); tick()
    d = list(DRAWN.values())[m0:]
    def ended(prefix):
        i = max((k for k, x in enumerate(d) if x.startswith(prefix)), default=None)
        return i is not None and i + 1 < len(d) and d[i + 1] == "end"
    check(ended("begin ##kkLapHud") and ended("begin kkLap  v"), "v0.24 #13: a throw inside the HUD and the rules window still calls im.End (%s)" % d[:6])
    C.globals().ui_imgui["failText"] = False; HUD["broken"] = False; rs.open = False
    # the timer window had this since v0.23: still holds
    fresh_track([(0, 0, 0), (100, 0, 0)])
    drive(100, [(0, 0, 0)])                                      # at the line: the timer window shows 0:00.000 (v0.26)
    for _ in range(120): tick()
    C.globals().ui_imgui["failText"] = True
    m0 = len(DRAWN); tick()
    d = list(DRAWN.values())[m0:]
    check(ended("begin ##kkLapTimer"), "v0.24 #13: the timer window too (%s)" % d[:4])
    C.globals().ui_imgui["failText"] = False; HUD["broken"] = False
    for _ in range(3): tick()
guard("v0.24 #13", s_v24_13)

# ---- 16: on|off commands with no argument show the value; BeamMP's unicycle is not a racer
def s_v24_16():
    before = {k: G.cfg[k] for k in ("autoStart", "markers", "announceSplits", "debug", "hud")}
    mark = len(chat_out)
    for c in ("/autostart", "/markers", "/announce", "/debug", "/hud"): chat(c)
    after = {k: G.cfg[k] for k in before}
    check(after == before and len([m for p, m in chat_out[mark:] if " is " in m and "on|off" in m]) == 5,
          "v0.24 #16: /autostart /markers /announce /debug /hud with no argument show the value, change nothing (%s)" % after)
    for c in ("/autostart on", "/markers on", "/debug on", "/hud on"): chat(c)   # put back whatever a bare command switched off
    fresh_track([(0, 0, 0), (100, 0, 0)], own=(100, 102), park=(-30, 0, 0))
    jbeams[102] = "unicycle"; positions[102] = (1, 0, 0); velocities[102] = (2.0, 0.0, 0.0)
    X0 = len(events_to_server)
    for _ in range(5): tick()
    drive(102, [(50, 0, 0), (100, 0, 0), (50, 0, 0)] + [(50, 0, 0)] * 60 + [(0, 0, 0)])
    sent = [n for n, d in events_to_server[X0:] if n in ("kkLap_bubble", "kkLap_cross") and json.loads(d).get("vid") == 102]
    check(sent == [] and "1:102" not in bubble_keys(), "v0.24 #16: the unicycle walks through the bubble and both checkpoints: no bubble entry, no crossing (%s)" % sent)
    del jbeams[102]; del positions[102]; own_vids[:] = [100]
guard("v0.24 #16", s_v24_16)

# ---- 11: a hot reload of main.lua re-sends the config; a client caught mid-countdown can honk again
def s_v24_11():
    fresh_track([(0, 0, 0), (100, 0, 0)])
    positions[100] = (0, 0, 0); velocities[100] = (0.0, 0.0, 0.0)
    for _ in range(3): tick()
    mark = len(chat_out); M.onHorn(100)
    for _ in range(20): tick()                                    # 1 s into the countdown: the client is counting
    check(said(mark, "race start in"), "v0.24 #11: (setup) countdown running")
    E0 = len(events_to_client)
    S.execute(SERVER.read_text()); G.onInit()                     # the plugin file is reloaded (server hot reload)
    check(any(p == -1 and n == "kkLap_cfg" for p, n, _ in events_to_client[E0:]), "v0.24 #11: a reload with players on sends the config to everyone")
    for _ in range(40): tick()
    X0 = len(events_to_server); mark = len(chat_out); M.onHorn(100)
    check(any(n == "kkLap_arm" for n, _ in events_to_server[X0:]) and said(mark, "race start in"),
          "v0.24 #11: after the reload the honk is heard and starts a countdown (client no longer stuck counting)")
    for _ in range(130): tick()
    velocities[100] = (27.78, 0.0, 0.0)
guard("v0.24 #11", s_v24_11)
check(not dropped, "no client event was dropped by the server (after the v0.24 scenarios)")

# ================================================================== v0.25: self-explaining logs (Darren, 30/10:
# "any more debugging you can write into this in case of failure next time, save me explaining it")
def s_v25_logs():
    fresh_track([(0, 0, 0), (100, 0, 0)], own=(100, 101), park=(0, 0, 0))
    positions[101] = (2, 0, 0); velocities[100] = (0.0, 0.0, 0.0); velocities[101] = (0.0, 0.0, 0.0)
    mark = len(server_log)
    for _ in range(5): tick()
    check(any("bubble +Darren#101 (now 2)" in l for l in server_log[mark:]) or any("bubble +Darren#100 (now 2)" in l for l in server_log[mark:]),
          "v0.25 b: a car entering the bubble is logged as bubble +name#vid (now N)")
    mark = len(server_log); M.onHorn(100)
    got = server_log[mark:]
    arm = [l for l in got if "arm Darren#100:" in l]
    check(arm and "1 of 2 ready" in arm[0] and "Darren#100 ready" in arm[0] and "Darren#101 waiting" in arm[0] and any("no countdown: waiting on Darren#101" in l for l in got),
          "v0.25 a: the honk logs the roster it looked at and who it waits on (%s)" % (arm[:1] or got[-3:]))
    mark = len(server_log); M.onHorn(101)
    check(any("arm Darren#101: 2 of 2 ready [Darren#100 ready, Darren#101 ready]" in l and l.endswith("-> countdown") for l in server_log[mark:]),
          "v0.25 a: the honk that starts it logs the same roster line ending -> countdown")
    for _ in range(120): tick()                                   # GO
    r = console("lap race")
    check("no countdown: GO was" in r, "v0.25: lap race right after a GO says why there is no countdown (%r)" % r)
    mark = len(server_log); positions[101] = (300, 300, 0); tick()
    check(any("bubble -Darren#101 (now 1)" in l for l in server_log[mark:]), "v0.25 b: a car leaving the bubble is logged as bubble -name#vid (now N)")
    r = console("lap status")
    check(isinstance(r, str) and "bubble: 1" in r and "race: active false" in r and "run Darren#100: next cp 2" in r and "adminChat true" in r and "trackRev" in r,
          "v0.25 f: lap status shows the race, the bubble roster, each run and the key cfg values")
    check("started (GO" in r, "v0.25 f: the bubble roster in lap status says ready/started per car")
    # c: refusals carry their numbers; a not-counted lap goes to laps.log with its reason
    mark = len(server_log)
    G.onCross(1, json.dumps({"vid": 555, "cp": 2}))
    check(any("cross refused Darren#555: got cp 2, wanted cp 1" in l for l in server_log[mark:]), "v0.25 c: a wrong checkpoint logs got cp and wanted cp")
    drive(100, [(100, 0, 0), (0, 0, 0)], step_ms=300)               # car 100's lap runs (101 drove out): cp2 and the line in 600 ms
    check(any("lap rejected, too short: Darren#100 600 ms < minLapMs 5000" in l for l in server_log[mark:]), "v0.25 c: a too-short lap logs its ms against minLapMs")
    G.onInvalid(1, json.dumps({"vid": 100, "reason": "bench teleport 40 m"})); clock_ms[0] += 3000
    G.onCross(1, json.dumps({"vid": 100, "cp": 2})); clock_ms[0] += 3000
    G.onCross(1, json.dumps({"vid": 100, "cp": 1}))
    check(any("LAP NOT COUNTED Darren#100 0:06.000 (invalidated: bench teleport 40 m)" in l for l in server_log[mark:])
          and "NOT COUNTED  0:06.000  (bench teleport 40 m)" in pathlib.Path("Resources/Server/kkLap/laps.log").read_text(),
          "v0.25 c: a lap not counted logs and writes to laps.log the reason it was invalidated")
    G.onCross(1, json.dumps({"vid": 556, "cp": 1, "go": True}))     # a stale client's GO cross (v0.25): just a line cross with no lap running
    check(any("cross Darren#556 with no lap running: no timing without a honk countdown" in l for l in server_log[mark:]),
          "v0.26 e: a line cross with no lap running starts nothing and says why")
    # g: one file to upload
    r = console("lap report")
    rp = pathlib.Path("Resources/Server/kkLap/report.txt")
    txt = rp.read_text() if rp.exists() else ""
    check("report written: Resources/Server/kkLap/report.txt" in r and all(k in txt for k in ("== status", "bubble: 1", "== config", '"adminChat"', "== board", "[KKLAP][CONSOLE] lap report")),
          "v0.25 g: lap report writes report.txt with status, config, board and the log tail, and replies with the path")
    # d: client version
    check(not any("[KKLAP][W] client" in l for l in server_log), "v0.25 d: the bench client (same version) gets no version warning")
    mark = len(server_log); mc = len(chat_out)
    G.onAck(1, json.dumps({"v": "0.20"})); G.onAck(1, json.dumps({"v": "0.20"})); G.onClientDbg(1, "extension v0.20 loaded, markers available")
    w = [l for l in server_log[mark:] if "[KKLAP][W] client Darren runs v0.20, server is v%s" % VER in l]
    told = [m for p, m in chat_out[mc:] if p == 1 and "kkLap: your client mod is v0.20, server is v%s, rejoin to re-download" % VER in m]
    check(len(w) == 1 and len(told) == 1, "v0.25 d: a client on another version: one [W] line and one chat line to that player (%d, %d)" % (len(w), len(told)))
    G.onAck(1, json.dumps({"v": VER}))
    # e: the alive relay, once a minute per client unless it changes
    sent0 = sum(1 for n, d in events_to_server if n == "kkLap_dbg" and d.startswith("alive"))
    mark = len(server_log)
    for _ in range(1300): tick()                                 # 65 s, own map unchanged
    sent = sum(1 for n, d in events_to_server if n == "kkLap_dbg" and d.startswith("alive")) - sent0
    logged = [l for l in server_log[mark:] if "[KKLAP][C1] alive" in l]
    check(sent >= 12 and 1 <= len(logged) <= 2, "v0.25 e: alive sent %d times in 65 s, logged %d (once a minute)" % (sent, len(logged)))
    mark = len(server_log); mc = len(client_log); own_vids[:] = [100]
    for _ in range(110): tick()
    check(any("[KKLAP][C1] alive, own vehicles: 100 (v%s)" % VER in l for l in server_log[mark:]), "v0.25 e: an alive line that says something new is logged at once")
    check(any("vehicle 101 pruned (no longer ours, was in bubble: no)" in l for l in client_log[mc:]), "v0.25 h: a pruned car is logged with whether it was in the bubble")
    # k: an error inside onUpdate is caught, counted, relayed once; the extension keeps running
    real = C.globals().MPVehicleGE["getOwnMap"]
    C.execute("MPVehicleGE.getOwnMap = function() error('bench: injected') end")
    mc, ms, err = len(client_log), len(server_log), None
    try:
        for _ in range(12): tick()
    except Exception as e: err = str(e).splitlines()[0]
    C.globals().MPVehicleGE["getOwnMap"] = real
    counts = {k: v for k, v in M.frameErrors.items() if "bench: injected" in k}
    check(err is None and list(counts.values()) == [12], "v0.25 k: an onUpdate error does not escape and is counted (%s, %s)" % (err, counts))
    check(sum("onUpdate error x1:" in l and "bench: injected" in l for l in client_log[mc:]) == 1
          and sum("[KKLAP][C1] ERROR onUpdate error x1:" in l for l in server_log[ms:]) == 1, "v0.25 k: logged once and relayed to the server once")
    X0 = len(events_to_server); velocities[100] = (27.78, 0.0, 0.0)
    check(any("run timed out Darren#100" in l for l in server_log[ms - 2000:]), "v0.27: (setup) the 65 s wait above timed the run out, so the car re-enters the bubble to cross")
    drive(100, [(50, 0, 0), (0, 0, 0)])
    for _ in range(200): tick()
    check(crosses(X0, 100) and any("onUpdate error x12:" in l for l in client_log[mc:]), "v0.25 k: the extension keeps ticking (car 100 still crosses) and the count x12 is logged")
    # l: why the lap state was reset or kept
    mc = len(client_log); chat("/hud on")
    check(any("same track (rev" in l and "lap state kept" in l for l in client_log[mc:]) and any("own map at cfg time: [100]" in l for l in client_log[mc:]),
          "v0.25 l: a config push logs same track, kept, and the own map")
    mc = len(client_log); chat("/lap reset")
    check(any("track changed (rev" in l and "lap state reset" in l for l in client_log[mc:]), "v0.25 l: /reset logs track changed, reset")
guard("v0.25 logs", s_v25_logs)
check(any("vehicle 100 spawned (own: yes)" in l for l in client_log), "v0.25 h: a spawned car is logged with whether it is ours")
check(any("vehicle 101 destroyed (game hook, was in bubble: yes" in l for l in client_log), "v0.25 h: a destroyed car is logged with whether it was in the bubble")
check(any("music: want art/sound/kkLap/" in l for l in client_log) and any("music playing art/sound/kkLap/kiwi.ogg vol 0.30 via playOnce" in l for l in client_log)
      and any("music: stopped (level exit)" in l for l in client_log), "v0.25 i: music decisions carry their reason (want, via playOnce, stopped on level exit)")
check(any("first draw: display 960 x 1080 px, big text" in l for l in client_log), "v0.25 j: the first HUD draw logs the display size and the big-text method")

# ---- v0.25: the console after the admin list is locked; adminChat off again
def s_v25_console():
    check(G.cfg["admins"] == S.table_from(["Darren"]) or list(G.cfg["admins"].values()) == ["Darren"], "v0.25: (setup) admins locked to Darren")
    check(console("lap admin list") == "admins: Darren", "v0.25: the console is always admin (pid -2), whoever is on the list")
    mark = len(chat_out); chat_as(2, "Bob", "/help"); chat("/help")
    bob = [m for p, m in chat_out[mark:] if p == 2]; dar = [m for p, m in chat_out[mark:] if p == 1]
    check(any("admins: use the server console" in m for m in bob) and not any("/mark" in m for m in bob) and any("/mark" in m for m in dar),
          "v0.25: adminChat on: /help shows admin commands to an admin only; a non-admin is told to use the console")
    r = console("lap adminchat off")
    check(G.cfg["adminChat"] == False and "console only" in r, "v0.25: lap adminchat off")
    mark = len(chat_out); cd = G.cfg["countdown"]; chat("/set countdown 9")
    check(G.cfg["countdown"] == cd and any(p == 1 and "admin commands run from the server console only" in m for p, m in chat_out[mark:])
          and any("chat command refused (adminChat off, console only): Darren: /set countdown 9" in l for l in kklog()),
          "v0.25: adminChat off: an admin's chat /set is refused too, and the refusal is logged")
    G.onPlayerJoin(1)
    check(rs.admin is None, "v0.25: adminChat off: the rules window carries no admin lines")
    rs.open = False
    own_vids[:] = [100]; seat0_vid[0] = 100; positions[100] = (50, 50, 0)
    n0 = len(G.cfg["checkpoints"]); mark = len(chat_out); ms = len(server_log)
    r = console("lap mark Darren 12"); M.onSurface(100, False, "FL=GRASS")
    cps = json.load(open(cfgfile))["checkpoints"]
    check(len(cps) == n0 + 1 and cps[-1]["r"] == 12 and any(p == 1 and "checkpoint %d marked" % (n0 + 1) in m for p, m in chat_out[mark:])
          and any("position from Darren (asked from the console): checkpoint %d marked" % (n0 + 1) in l for l in server_log[ms:]),
          "v0.25: lap mark Darren 12 marks at Darren's car with r 12; the result goes to Darren and to the console log")
guard("v0.25 console", s_v25_console)

# ================================================================== v0.26 (Darren, 30/10): the start bubble is a waiting area, honk only
# "the timer starts when enter bubble, no, it should start at zero"; "make sure the trigger for timer ONLY occurs when
# countdown has been activated via honk"; "honk to start countdown, then drive out of bubble to start timer, first one
# out starts timer"; "if someone else comes in after, it can run another race"; "3 lights to go". The server runs the
# whole start sequence from its bubble roster (enter/leave with speed, honk); the client senses and draws.
console("lap adminchat on")                                  # the scenario above left it off: fresh_track() marks through chat
DARK, RED, GREEN = "0.25,0.25,0.25", "1.00,0.15,0.15", "0.20,1.00,0.20"
def frame_items():
    """one tick; what the imgui stub drew during it, as (text, colour)"""
    m0 = len(DRAWN); tick()
    return [(DRAWN[i], COLOURS[i]) for i in range(m0 + 1, len(DRAWN) + 1)]
def circles(items): return [d[7:] for d, c in items if d.startswith("circle ")]
def hud_lines(items):
    """the entries drawn inside the ##kkLapHud window of one frame"""
    out, on = [], False
    for d, c in items:
        if d.startswith("begin "): on = (d == "begin ##kkLapHud"); continue
        if d == "end": on = False; continue
        if on: out.append((d, c))
    return out

def s_v26_bubble():
    fresh_track([(0, 0, 0), (100, 0, 0)])                        # car 100 parked at (-30,0,0), 100 km/h along x
    # (a) a fresh car drives into the start bubble: at the line, clock at zero, nothing starts
    E0 = len(events_to_client); X0 = len(events_to_server); mf = len(flashes)
    positions[100] = (0, 0, 0); tick(); t_in = clock_ms[0]
    c = crosses(X0, 100)
    check(infos(E0, 100) == ["at the line"] and len(c) == 1 and c[0].get("restart") is True and c[0]["cp"] == 1 and round(c[0]["kph"]) == 100,
          "v0.26 a: a fresh car driving into the start bubble: one 'at the line' cross, the server answers at the line, no timing started")
    for _ in range(40): tick()                                    # sits 2 s in the bubble
    fr = tframes(t_in, clock_ms[0])
    check(fr and all(f and texts(f) == ["x3 0:00.000", "x1 in the start bubble: honk to race"] and f[0][1] == "1.00,0.75,0.10" for f in fr),
          "v0.26 a: the timer window shows 0:00.000 big in amber (v0.27) with the honk hint, and the clock stays at zero for the 2 s it sits there")
    check(not any("timing started" in m for m in infos(E0, 100)) and "TIMING STARTED" not in flashes[mf:], "v0.26 a: nothing started the clock")
    # (b) drives out without honking: nothing starts, the window hides; cp2 is not a split; back in = at the line again
    E1 = len(events_to_client); X1 = len(events_to_server); mf = len(flashes); L0 = len(laps())
    positions[100] = (50, 0, 0); tick()
    check(flashes[mf:] == ["no timing: honk in the start bubble to race"], "v0.26 b: driving out with no honk flashes the hint once (%s)" % flashes[mf:])
    for _ in range(10): tick()
    check(all(f is None for _, f in timer_frames[-10:]) and M.timerVid() is None, "v0.26 b: no own car timing: the timer window is gone")
    drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 5)
    check(infos(E1, 100) == [] and crosses(X1, 100) == [] and not any(n == "kkLap_split" for p, n, d in events_to_client[E1:])
          and flashes[mf:] == ["no timing: honk in the start bubble to race"],
          "v0.26 b: through cp2 with no lap: no cross sent, no split, no timing started, the hint only once")
    positions[100] = (0, 0, 0); tick()
    check(infos(E1, 100) == ["at the line"] and len(laps()) == L0, "v0.26 b: back into the bubble: at the line again, no lap")
    # (e) a full drive round with no honk; and the server refuses a bare line cross with no lap running
    E2 = len(events_to_client)
    drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])
    check(len(laps()) == L0 and not any("timing started" in m for m in infos(E0, 100)), "v0.26 e: a fresh car in, out, round the track and back in: zero laps, zero timing started")
    ms = len(server_log); G.onCross(1, json.dumps({"vid": 100, "cp": 1}))   # a bare line cross with no lap running (a stale client)
    check(any("cross Darren#100 with no lap running: no timing without a honk countdown" in l for l in server_log[ms:]) and infos(E2, 100)[-1] == "at the line",
          "v0.26 e: the server refuses a line cross with no lap running: logged, answered at the line")
    # (f) honk, countdown, GO, out: timing started; lap 1 and lap 2 by drive-through, no further honk
    E3 = len(events_to_client); L0 = len(laps()); ms = len(server_log)
    race_start([100]); Xh = len(events_to_server)
    positions[100] = (50, 0, 0); tick(); t1 = clock_ms[0]
    check(infos(E3, 100) == ["GO start", "timing started"] and any(re.search(r"race \d+ clock started by Darren#100 leaving the bubble at 100 km/h", l) for l in server_log[ms:]),
          "v0.26 f: honk, countdown, GO, drive out: timing started at the exit, logged as the first car out (%s)" % infos(E3, 100))
    drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)]); tA = clock_ms[0]
    drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)]); tB = clock_ms[0]
    got = laps()[L0:]
    check(len(got) == 2 and got[0]["lapMs"] == tA - t1 and got[1]["lapMs"] == tB - tA and not any(n == "kkLap_arm" for n, _ in events_to_server[Xh:]),
          "v0.26 f: lap 1 from the exit, lap 2 from the line (drive-through), no further honk (%s)" % [l["lapMs"] for l in got])
    # (c) back into the bubble mid-lap: at the line, 0:00.000 held, honk to go again
    E4 = len(events_to_client); ms = len(server_log)
    positions[100] = (50, 0, 0); tick(); positions[100] = (0, 0, 0); tick(); t_back = clock_ms[0]
    check(infos(E4, 100) == ["at the line"] and any("at the line pid=1 vid=100 (partial lap dropped)" in l for l in server_log[ms:]),
          "v0.26 c: back into the bubble mid-lap: at the line, the partial lap dropped (the server dbg line says so)")
    for _ in range(10): tick()
    fr = tframes(t_back, clock_ms[0])
    check(fr and all(f and texts(f)[0] == "x3 0:00.000" and texts(f)[1] == "x1 in the start bubble: honk to race" and texts(f)[-1] == "x1 best " + fmt(got[0]["bestMs"]) + "   lap 2" for f in fr),
          "v0.26 c: the clock is held at 0:00.000 with the honk hint; the best line stays")
    E5 = len(events_to_client)
    race_start([100]); positions[100] = (50, 0, 0); tick()
    check(infos(E5, 100) == ["GO start", "timing started"], "v0.26 c: honk, GO, out: timing started again (%s)" % infos(E5, 100))
    # a honk outside the bubble: the server's roster refuses it
    ms = len(server_log); mc = len(chat_out); M.onHorn(100)
    check(any("arm Darren#100 refused: not in the start bubble" in l for l in server_log[ms:]) and not said(mc, "race start in"),
          "v0.26: a honk outside the start bubble is refused by the server from its roster, no countdown")
    # (g) the server source sets lapStart to a time in exactly one place
    sets = [m for m in re.findall(r"\blapStart\s*=\s*([^\s,;]+)", SERVER.read_text()) if m != "nil"]
    check(sets == ["t"], "v0.26 g: main.lua assigns lapStart a time in exactly one place, startLap (%s)" % sets)
guard("v0.26 bubble", s_v26_bubble)

def s_v26_lights():
    fresh_track([(0, 0, 0), (100, 0, 0)])
    positions[100] = (0, 0, 0); velocities[100] = (0.0, 0.0, 0.0)
    for _ in range(3): tick()
    LS = M.lightsState
    check(LS["on"] == False and circles(frame_items()) == [], "v0.26 lights: none before the countdown")
    M.onHorn(100)
    it = frame_items()
    check(circles(it) == [DARK, DARK, DARK] and any(d.startswith("begin ##kkLapHud") for d, _ in it), "v0.26 lights: at 5 three dark circles in the HUD window (%s)" % circles(it))
    seen = {}
    for _ in range(130):
        it = frame_items(); n = flashes[-1]
        if n in ("4", "3", "2", "1", "GO!") and circles(it): seen.setdefault(n, circles(it))
        if n == "GO!" and len(seen) >= 5: break
    check(seen.get("4") == [DARK] * 3 and seen.get("3") == [RED, DARK, DARK] and seen.get("2") == [RED, RED, DARK] and seen.get("1") == [RED] * 3 and seen.get("GO!") == [GREEN] * 3,
          "v0.26 lights: 4 dark, 3 one red, 2 two red, 1 three red, GO three green (%s)" % seen)
    for _ in range(50): tick()                                    # 2.5 s after GO
    check(LS["on"] == False and circles(frame_items()) == [], "v0.26 lights: gone 2.5 s after GO")
    # no draw list in the binding: three 'O' at scale 3, SameLine between, the HUD survives
    C.globals().ui_imgui["noDrawList"] = True
    mc = len(client_log)
    race_start([100])                                             # waits out the 10 s window, honks: the countdown runs with text lights
    M.onHorn(100)                                                 # (the GO just happened: ignored for 10 s) -> wait and honk again
    for _ in range(205): tick()
    M.onHorn(100)
    it = frame_items(); hl = hud_lines(it)
    os_ = [(d, c) for d, c in hl if d == "x3 O"]
    check(circles(it) == [] and os_ == [("x3 O", DARK)] * 3 and [d for d, _ in hl].count("sameline") == 2 and not HUD["broken"]
          and sum("lights: draw list missing, text lights" in l for l in client_log[mc:]) == 1,
          "v0.26 lights: no draw list: three 'O' text lights at scale 3 with SameLine between, HUD not broken, said once (%s)" % hl)
    greens = None
    for _ in range(130):
        it = frame_items()
        if flashes[-1] == "GO!" and hud_lines(it): greens = [(d, c) for d, c in hud_lines(it) if d == "x3 O"]; break
    check(greens == [("x3 O", GREEN)] * 3 and not HUD["broken"] and sum("lights: draw list missing" in l for l in client_log[mc:]) == 1,
          "v0.26 lights: text lights go green at GO, the HUD is not broken, the fallback was logged once (%s)" % greens)
    C.globals().ui_imgui["noDrawList"] = False; HUD["lightsMode"] = None
    velocities[100] = (27.78, 0.0, 0.0)
guard("v0.26 lights", s_v26_lights)

def s_v26_races():
    fresh_track([(0, 0, 0), (100, 0, 0)], own=(100, 101, 102))
    positions[101] = (-30, 5, 0); positions[102] = (-30, 10, 0)
    for v in (100, 101, 102): velocities[v] = (27.78, 0.0, 0.0)
    for _ in range(3): tick()
    E0 = len(events_to_client); ms = len(server_log)
    race_start([100, 101])                                        # race 1: A and B honk, GO
    race1 = int(re.search(r"GO at \d+, race (\d+)", "\n".join(server_log[ms:])).group(1))
    positions[100] = (50, 0, 0); tick(); t1 = clock_ms[0]        # A out: the clock of this race starts for A and B
    check(infos(E0, 100) == ["at the line", "GO start", "timing started"] and infos(E0, 101) == ["at the line", "GO start", "timing started"],
          "v0.26 races: A out starts the clock for A and B (%s / %s)" % (infos(E0, 100), infos(E0, 101)))
    for _ in range(13): tick()
    positions[101] = (50, 0, 0); tick()                           # B out 700 ms later
    E1 = len(events_to_client); ms = len(server_log)
    race_start([102])                                             # C arrives after the 10 s window, honks alone: race 2
    race2 = int(re.search(r"GO at \d+, race (\d+)", "\n".join(server_log[ms:])).group(1))
    check(race2 == race1 + 1 and infos(E1, 102) == ["at the line", "GO start"] and infos(E1, 100) == [] and infos(E1, 101) == [],
          "v0.26 races: a late car honks alone: its own GO, race %d; the cars already racing hear nothing" % race2)
    positions[102] = (50, 0, 0); tick(); t2 = clock_ms[0]
    check(infos(E1, 102) == ["at the line", "GO start", "timing started"] and infos(E1, 100) == [] and infos(E1, 101) == []
          and any("race %d clock started by Darren#102 leaving the bubble" % race2 in l for l in server_log[ms:]),
          "v0.26 races: C out starts race %d's clock, logged; A and B untouched" % race2)
    L0 = len(laps()); mc = len(chat_out)
    drive(100, [(100, 0, 0), (50, 0, 0), (0, 0, 0)]); tA = clock_ms[0]
    drive(101, [(100, 0, 0), (50, 0, 0), (0, 0, 0)]); tB = clock_ms[0]
    drive(102, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)]); tC = clock_ms[0]
    got = laps()[L0:]
    lines = said(mc, "  lap ", -1)
    check(len(got) == 3 and got[0]["lapMs"] == tA - t1 and got[1]["lapMs"] == tB - t1 and got[2]["lapMs"] == tC - t2,
          "v0.26 races: A and B timed from A's exit (B's 700 ms late start included), C from its own exit (%s)" % [l["lapMs"] for l in got])
    check(len(lines) == 3 and lines[0].endswith("race %d" % race1) and lines[1].endswith("race %d" % race1) and lines[2].endswith("race %d" % race2),
          "v0.26 races: the lap lines carry race %d, %d and %d (%s)" % (race1, race1, race2, [l[-10:] for l in lines]))
    r = console("lap status")
    check("race: active false" in r and "run Darren#102: next cp 2" in r, "v0.26 races: lap status lists the runs (%s)" % (r or "")[:80])
guard("v0.26 races", s_v26_races)
check(not dropped, "no client event was dropped by the server (after the v0.26 scenarios)")

# ================================================================== v0.27 (Darren, 31/10, after a live evening with three drivers:
# "Works mostly really well, just some little bugs and improvements."). Overlay only for players in a race; a run is
# dropped after 30 s without a checkpoint; nameless board entries; standing + rolling laps both count; car make and
# model; a run is maxLaps laps; admin commands in chat again (with the console kill switch); amber clock with a shadow.
def s_v27_admin():
    check(G.cfg["adminChat"] == True and list(G.cfg["admins"].values()) == ["Darren"], "v0.27 admin: (setup) adminChat on, admins locked to Darren")
    mark = len(chat_out); chat_as(2, "Bob", "/set countdown 9")
    check(G.cfg["countdown"] == 5 and said(mark, "admin only", 2), "v0.27 admin: a non-admin's chat /set is refused once admins are set")
    mark = len(chat_out); chat("/set countdown 6")
    check(G.cfg["countdown"] == 6 and said(mark, "countdown = 6", 1), "v0.27 admin: an admin's chat /set works again (adminChat on)")
    chat("/set countdown 5")
    mark = len(chat_out); chat("/admin add me")
    check(list(G.cfg["admins"].values()) == ["Darren", "id:42"] and said(mark, "id:42 is now a kkLap admin") and json.load(open(cfgfile))["admins"] == ["Darren", "id:42"],
          "v0.27 admin: /admin add me stores the typist's account key id:42, saved")
    MPT = S.globals().MP; real_name = MPT["GetPlayerName"]
    MPT["GetPlayerName"] = lambda pid: "photon_dazz" if pid == 1 else "p%d" % pid      # same account, another display name
    mark = len(chat_out); chat_as(1, "photon_dazz", "/set countdown 7")
    check(G.cfg["countdown"] == 7 and said(mark, "countdown = 7", 1), "v0.27 admin: isAdmin matches the account key under a different display name")
    MPT["GetPlayerName"] = real_name; chat("/set countdown 5")
    check(console("lap admin remove id:42") == "admins: Darren" and console("lap admin add me").startswith("admin add me works from game chat only"),
          "v0.27 admin: the console removes the key; 'me' means nothing on the console")
    mark = len(chat_out); chat("/help")
    check(any(p == 1 and "/set minLapMs" in m and "timeoutS" in m and "maxLaps" in m for p, m in chat_out[mark:]), "v0.27 admin: chat /help lists the admin commands for an admin, timeoutS and maxLaps among the /set keys")
    G.onPlayerJoin(1)
    check(rs.admin is not None and any("timeoutS" in l for l in rs.admin.values()), "v0.27 admin: the rules window carries the admin lines again")
    rs.open = False
    check("timeoutS|pointsMinDrivers" in console("lap help") and "maxLaps" in console("lap help"), "v0.27 admin: lap help on the console lists them too")
    # the kill switch: lap adminchat off refuses every admin command from every client, admins included, and cannot be flipped from chat
    r = console("lap adminchat off"); mark = len(chat_out); n0 = len(kklog())
    chat("/set countdown 9")
    check(r.startswith("adminchat off") and G.cfg["countdown"] == 5 and said(mark, "admin commands run from the server console only", 1)
          and any("chat command refused (adminChat off, console only): Darren: /set countdown 9" in l for l in kklog()[n0:]),
          "v0.27 kill switch: lap adminchat off -> an admin's chat /set is refused with the console-only line and logged")
    mark = len(chat_out); chat("/adminchat on"); chat("/lap adminchat on")
    check(G.cfg["adminChat"] == False and len(said(mark, "adminchat is set from the server console only", 1)) == 2, "v0.27 kill switch: /adminchat on from chat is refused twice over and changes nothing")
    r = console("lap adminchat on"); mark = len(chat_out); chat("/set countdown 6")
    check(r.startswith("adminchat on") and G.cfg["countdown"] == 6 and said(mark, "countdown = 6", 1), "v0.27 kill switch: console lap adminchat on restores chat control")
    chat("/set countdown 5")
guard("v0.27 admin", s_v27_admin)

def s_v27_engaged():
    fresh_track([(0, 0, 0), (100, 0, 0)], own=(100, 101))
    positions[101] = (50, 0, 0); velocities[101] = (0.0, 0.0, 0.0)     # parked 50 m from the start, not racing
    for _ in range(3): tick()
    m0 = len(DRAWN); tick(); tick()
    check(not M.engaged() and not any(d.startswith("begin ##kkLap") for d in list(DRAWN.values())[m0:]), "v0.27 engaged: two own cars outside the bubble, nothing running: not engaged, no kkLap window drawn")
    deliver_to_client("kkLap_hud", json.dumps({"msg": "someone else ready"})); mt = len(toasts); m0 = len(DRAWN); tick()
    check(any(t == "someone else ready" for t, _ in hud_items()) and not any(d.startswith("begin ##kkLapHud") for d in list(DRAWN.values())[m0:]),
          "v0.27 engaged: a HUD line that arrives while not engaged is queued, not drawn")
    positions[100] = (0, 0, 0); tick(); tick()
    check(M.engaged() and timer_frames[-1][1] is not None, "v0.27 engaged: car 100 in the bubble: engaged, the timer window shows")
    E0 = len(events_to_client); mc = len(chat_out); mf = len(flashes)
    M.onHorn(100)
    for _ in range(25): tick()
    sent = [(p, n) for p, n, d in events_to_client[E0:] if n in ("kkLap_count", "kkLap_hud")]
    digits = [p for p, m in chat_out[mc:] if "race start in" in m or m == "[LAP] 4"]
    check(len(sent) >= 3 and all(p == 1 for p, n in sent) and len(digits) == 2 and all(p == 1 for p in digits),
          "v0.27 engaged: kkLap_count, kkLap_hud and the chat countdown go to the racer's pid 1, none to -1 (%s)" % sorted(set(sent)))
    check(flashes[mf:][:2] == ["5", "4"], "v0.27 engaged: the digits flash while engaged (%s)" % flashes[mf:][:2])
    own_vids[:] = [101]                                             # car 100 is not ours any more, mid-countdown
    for _ in range(25): tick()                                      # the 1 s sweep prunes it and tells the server
    mf = len(flashes); m0 = len(DRAWN); E1 = len(events_to_client)
    for _ in range(80): tick()                                      # the countdown runs out on the server, nobody in it
    check(not M.engaged() and flashes[mf:] == [] and not any(d.startswith("begin ##kkLap") for d in list(DRAWN.values())[m0:]),
          "v0.27 engaged: the engaged car pruned: no digit, no GO, no window drawn (%s)" % flashes[mf:])
    check(not any(n in ("kkLap_count", "kkLap_go", "kkLap_hud") for p, n, d in events_to_client[E1:]) and not any("race start" in m or m.endswith("GO!") for p, m in chat_out[mc:] if p == -1),
          "v0.27 engaged: server: a countdown with nobody in the bubble and no run goes to nobody, never to -1")
    own_vids[:] = [100, 101]
    for _ in range(3): tick()                                       # car 100 ours again, in the bubble: at the line
    check(M.engaged(), "v0.27 engaged: (setup) back in the bubble, engaged")
    positions[100] = (20, 0, 0); tick(); tick()
    check(M.engaged() and timer_frames[-1][1] is None, "v0.27 engaged: 20 m out of the bubble without a honk: still engaged (hysteresis), no clock to show")
    positions[100] = (40, 0, 0); tick(); tick()
    check(not M.engaged(), "v0.27 engaged: 40 m out: not engaged, the overlay is gone")
    velocities[101] = (27.78, 0.0, 0.0)
guard("v0.27 engaged", s_v27_engaged)

def s_v27_timeout():
    fresh_track([(0, 0, 0), (100, 0, 0)])
    check(G.cfg["timeoutS"] == 30 and "timeoutS 30" in console("lap status"), "v0.27 timeout: timeoutS 30 by default, shown by lap status")
    drive(100, [(0, 0, 0)]); race_start([100]); drive(100, [(50, 0, 0)]); drive(100, [(100, 0, 0)])   # out, cp2
    E0 = len(events_to_client); mf = len(flashes); mc = len(chat_out); ms = len(server_log)
    for _ in range(580): tick()                                     # 29 s with no checkpoint
    f = timer_frames[-1][1]
    check(infos(E0, 100) == [] and f and RUN.match(texts(f)[0]), "v0.27 timeout: 29 s without a checkpoint: the run is alive, the clock runs")
    for _ in range(40): tick()                                      # 31 s
    check(infos(E0, 100) == ["timed out"] and any("run timed out Darren#100: 3" in l and "since the last checkpoint (limit 30)" in l for l in server_log[ms:]),
          "v0.27 timeout: 31 s: the server drops the run, logs it, tells the client")
    check(said(mc, "lap timed out: 30 s without a checkpoint, honk in the start bubble to go again", 1) and flashes[mf:] == ["TIMED OUT"],
          "v0.27 timeout: the driver is told in chat and gets one TIMED OUT flash (%s)" % flashes[mf:])
    check(M.timerVid() is None and timer_frames[-1][1] is None and "run Darren#100: next cp 1" in console("lap status") and not M.engaged(),
          "v0.27 timeout: the clock is gone, the run is back at the line, the player is not engaged any more")
    drive(100, [(50, 0, 0), (0, 0, 0)]); race_start([100]); drive(100, [(50, 0, 0)]); t1 = clock_ms[0]
    for _ in range(580): tick()
    drive(100, [(100, 0, 0)])
    for _ in range(580): tick()
    L0 = len(laps()); drive(100, [(50, 0, 0), (0, 0, 0)])
    got = laps()[L0:]
    check(len(got) == 1 and got[0]["lapMs"] == clock_ms[0] - t1 and got[0]["lapMs"] > 58000, "v0.27 timeout: a lap with two 29 s sectors is counted in full (%s)" % [l["lapMs"] for l in got])
    E1 = len(events_to_client); mf = len(flashes)
    race_start([100])                                               # the car sits in the bubble, honks, GO, never drives out
    for _ in range(640): tick()                                     # 32 s
    check(infos(E1, 100) == ["GO start", "timed out"] and flashes[mf:][-1] == "TIMED OUT", "v0.27 timeout: a GO starter that never leaves the bubble times out 30 s after the GO (%s)" % infos(E1, 100))
    check(console("lap set timeoutS 45") == "timeoutS = 45" and "timeoutS 45" in console("lap status"), "v0.27 timeout: lap set timeoutS 45 (chat /set too, item 7)")
    console("lap set timeoutS 30")
guard("v0.27 timeout", s_v27_timeout)

def s_v27_board():
    jbeams[100] = "etk800"
    for k in list(M.carNames.keys()): M.carNames[k] = None
    fresh_track([(0, 0, 0), (100, 0, 0)])
    check(console("lap set maxLaps 2") == "maxLaps = 2", "v0.27 run: (setup) maxLaps 2, the default")
    X0 = len(events_to_server); ml = len(client_log)
    drive(100, [(0, 0, 0)])
    check(any(n == "kkLap_bubble" and json.loads(d).get("car") == "ETK 800-Series" for n, d in events_to_server[X0:]) and sum("vid 100 is ETK 800-Series (etk800)" in l for l in client_log[ml:]) == 1,
          "v0.27 car: entering the bubble names the car 'ETK 800-Series' (core_vehicles.getModel stub), logged once")
    race_start([100])
    check(any(n == "kkLap_arm" and json.loads(d).get("car") == "ETK 800-Series" for n, d in events_to_server[X0:]) and S.globals().bubble["1:100"]["car"] == "ETK 800-Series",
          "v0.27 car: the honk carries the car, the server's bubble entry keeps it")
    E0 = len(events_to_client); mc = len(chat_out); ms = len(server_log)
    drive(100, [(50, 0, 0)]); t1 = clock_ms[0]
    drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)]); tA = clock_ms[0]        # the standing lap, 5.2 s
    lapA = laps()[-1]
    check(lapA["lapType"] == "standing" and lapA["car"] == "ETK 800-Series" and lapA["lapMs"] == tA - t1, "v0.27 laps: the lap out of the bubble is 'standing' and carries the car")
    last = pathlib.Path("Resources/Server/kkLap/laps.log").read_text().splitlines()[-1]
    check("  lap 1  " + fmt(lapA["lapMs"]) + "  standing  [" in last and "  in ETK 800-Series" in last, "v0.27 laps: laps.log: 'lap 1  m:ss.mmm  standing  [...]  ... in ETK 800-Series' (%s)" % last[20:])
    e = board()["id:42"]
    check(e["bestStandingMs"] == lapA["lapMs"] and e.get("bestRollingMs") is None and e["bestMs"] == lapA["lapMs"] and e["car"] == "ETK 800-Series" and e["sectors"] == lapA["sectors"] and e["bestType"] == "standing",
          "v0.27 board: one standing lap: bestStandingMs set, no rolling, bestMs = standing with its sectors and car")
    check(infos(E0, 100) == ["timing started"], "v0.27 run: lap 1 does not end the run")
    drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 55 + [(0, 0, 0)]); tB = clock_ms[0]   # the rolling lap, slower
    lapB = laps()[-1]; e = board()["id:42"]
    check(lapB["lapType"] == "rolling" and lapB["lapMs"] == tB - tA and lapB["lapMs"] > lapA["lapMs"] and e["bestRollingMs"] == lapB["lapMs"] and e["bestMs"] == lapA["lapMs"] and e["laps"] == 2,
          "v0.27 board: a slower rolling lap: bestRollingMs set, bestMs stays the standing one")
    check(infos(E0, 100) == ["timing started", "run complete"] and said(mc, "run complete: back to the start line and honk to go again", 1)
          and any("run complete Darren#100: 2 laps, back to the start line" in l for l in server_log[ms:]), "v0.27 run: the second lap completes the run: info, chat line, log")
    st = console("lap status")
    check("run Darren#100: next cp 1" in st and "laps 0" in st, "v0.27 run: the run is reset, next cp 1, laps 0")
    for _ in range(115): tick()                                    # 5.75 s of the 6 s hold
    hold = tframes(tB, tB + 5800)
    check(hold and all(f and texts(f)[0] == "x3 LAP  " + fmt(lapB["lapMs"]) and texts(f)[1] == "x1 RUN COMPLETE - back to the start line to go again"
                       and texts(f)[-1] == "x1 best " + fmt(lapA["lapMs"]) + "   lap 2" for f in hold),
          "v0.27 run: the timer holds the LAP line 6 s with 'RUN COMPLETE - back to the start line to go again' under it")
    for _ in range(20): tick()
    check(timer_frames[-1][1] is None and M.timerVid() is None and M.engaged(), "v0.27 run: 6 s later the timer window is gone; in the bubble the player stays engaged")
    L1 = len(laps()); E1 = len(events_to_client); X1 = len(events_to_server)
    drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])
    c = crosses(X1, 100)
    check(len(laps()) == L1 and infos(E1, 100) == ["at the line"] and [(x["cp"], x.get("restart")) for x in c] == [(1, True)],
          "v0.27 run: a third drive round after run complete is no lap: no cp2 cross, back in = at the line (%s)" % infos(E1, 100))
    E2 = len(events_to_client)
    race_start([100]); drive(100, [(50, 0, 0)]); t2 = clock_ms[0]
    drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)]); tC = clock_ms[0]        # standing again, 5.2 s
    drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 48 + [(0, 0, 0)]); tD = clock_ms[0]   # rolling, 5.1 s: faster than everything
    lapD = laps()[-1]; e = board()["id:42"]
    check(infos(E2, 100) == ["GO start", "timing started", "run complete"] and lapD["lapType"] == "rolling" and lapD["lapMs"] == tD - tC == 5100
          and e["bestRollingMs"] == 5100 and e["bestMs"] == 5100 and e["bestType"] == "rolling" and e["bestStandingMs"] == lapA["lapMs"] and e["sectors"] == lapD["sectors"] and e["laps"] == 4,
          "v0.27 board: a new run; a faster rolling lap: bestMs follows it with its sectors, the standing best stays, 4 laps (%s)" % {k: e.get(k) for k in ("bestMs", "bestStandingMs", "bestRollingMs", "laps")})
    tj = json.load(open("Resources/Server/kkLap/times.json"))["id:42"]
    check(tj["bestStandingMs"] == lapA["lapMs"] and tj["bestRollingMs"] == 5100 and tj["car"] == "ETK 800-Series" and tj["standing"]["car"] == "ETK 800-Series",
          "v0.27 board: times.json carries bestStandingMs, bestRollingMs and the car")
    r = console("lap times")
    check("standing " + fmt(lapA["lapMs"]) + "  rolling 0:05.100  ETK 800-Series" in r, "v0.27 board: lap times prints both laps and the car (%r)" % r[:90])
    check(console("lap set maxLaps 3") == "maxLaps = 3" and "maxLaps 3" in console("lap status"), "v0.27 run: lap set maxLaps 3")
    E3 = len(events_to_client); L3 = len(laps())
    race_start([100]); drive(100, [(50, 0, 0)])
    drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])
    for _ in range(2): drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])
    got = laps()[L3:]
    check(len(got) == 3 and got[2]["laps"] == 3 and infos(E3, 100) == ["GO start", "timing started", "run complete"], "v0.27 run: with maxLaps 3 the third lap counts and ends the run (%s)" % infos(E3, 100))
    console("lap set maxLaps 99")
    # car name fallback: core_vehicles.getModel missing -> the jbeam key, nothing throws
    C.execute("core_vehicles.kept = core_vehicles.getModel; core_vehicles.getModel = nil")
    for k in list(M.carNames.keys()): M.carNames[k] = None
    X2 = len(events_to_server); errs = dict(M.frameErrors.items())
    for _ in range(10): tick()
    drive(100, [(50, 0, 0), (0, 0, 0)])
    cars = [json.loads(d).get("car") for n, d in events_to_server[X2:] if n == "kkLap_bubble" and json.loads(d).get("inside")]
    check(cars == ["etk800"] and dict(M.frameErrors.items()) == errs, "v0.27 car: getModel missing: the key 'etk800' is the name, nothing throws (%s)" % cars)
    C.execute("core_vehicles.getModel = core_vehicles.kept")
    for k in list(M.carNames.keys()): M.carNames[k] = None
    del jbeams[100]
guard("v0.27 board", s_v27_board)

# ---- v0.28 (log 31/10): a standing lap must reach the board even when the run then times out (the live
# config.json still held minLaps 2); a timed-out run's lap count must not leak into the next run; maxLaps 0 =
# no limit; every counted lap goes into the board entry's history; set names in any case.
def s_v28():
    fresh_track([(0, 0, 0), (100, 0, 0)])
    G.cfg["minLaps"] = 2                                              # what the live config.json still carries: must be ignored
    check(console("lap set maxLaps 2") == "maxLaps = 2", "v0.28: (setup) maxLaps 2, as on the live server")
    b0 = board().get("id:42", {}); h0 = len(b0.get("history", [])); n0 = b0.get("laps", 0); ms = len(server_log)
    drive(100, [(0, 0, 0)]); race_start([100]); drive(100, [(50, 0, 0)]); t1 = clock_ms[0]
    drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)]); lapA = laps()[-1]
    drive(100, [(50, 0, 0)])
    E0 = len(events_to_client)
    for _ in range(640): tick()                                       # 32 s, no checkpoint: the run times out after lap 1
    e = board()["id:42"]; h = e.get("history", [])
    check(infos(E0, 100) == ["timed out"] and len(h) == h0 + 1 and h[0]["ms"] == lapA["lapMs"] and h[0]["type"] == "standing" and e["laps"] == n0 + 1,
          "v0.28: one standing lap then a timeout: the lap is on the board and first in its history (%s)" % (h[:1],))
    check(h[0]["car"] == lapA.get("car") and h[0]["sectors"] == lapA["sectors"] and isinstance(h[0]["at"], int) and h[0]["at"] > 1600000000,
          "v0.28: a history row carries car, sectors and the time it was driven")
    check(not any("not on the board yet" in l for l in server_log[ms:]), "v0.28: no 'not on the board yet' line any more")
    E1 = len(events_to_client); L1 = len(laps())
    drive(100, [(50, 0, 0), (0, 0, 0)]); race_start([100]); drive(100, [(50, 0, 0)])
    drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])
    got = laps()[L1:]
    check(len(got) == 1 and got[0]["laps"] == 1 and got[0]["lapType"] == "standing" and "run complete" not in infos(E1, 100),
          "v0.28: the next run's first lap is lap 1, standing, and does not end the run (live: 'lap 2 standing' then 'run complete') (%s)" % infos(E1, 100))
    check(console("lap set maxlaps 0") == "maxLaps = 0" and "maxLaps 0 (no limit)" in console("lap status"), "v0.28: lap set maxlaps 0 (any case) = no limit, lap status says so")
    for _ in range(4): drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])
    got = laps()[L1:]
    check(len(got) == 5 and got[-1]["laps"] == 5 and "run complete" not in infos(E1, 100) and M.timerVid() == 100,
          "v0.28: maxLaps 0: five laps in one run, no run complete, the clock still runs (%s)" % infos(E1, 100))
    h = board()["id:42"]["history"]
    check([x["ms"] for x in h[:5]] == [l["lapMs"] for l in reversed(got)], "v0.28: history is newest first, one row per counted lap")
    r = console("lap set 2")
    check(r.startswith("set needs a name and a number") and "maxLaps" in r and "minLaps" not in r, "v0.28: 'lap set 2' explains the form and lists the names (%r)" % r[:60])
    check(console("lap set maxLaps 101").startswith("maxLaps must be 0..100"), "v0.28: maxLaps above 100 refused")
    G.cfg["minLaps"] = None
    for _ in range(640): tick()
    console("lap set maxLaps 99")
guard("v0.28", s_v28)

# ---- v0.29: every lap knows its map (server MP.Get first, a client's getMissionFilename as fallback); the board keeps
# a best per map + car; history 100; times.json carries _meta; a car swapped on the same vid is named afresh.
def s_v29():
    check(any("map: this BeamMP build cannot tell the plugin its map" in l for l in server_log), "v0.29 map: no MP.Get on this (bench) server: said once at load")
    C.execute("getMissionFilename = function() return '/levels/west_coast_usa/main.level.json' end")
    ms = len(server_log)
    fresh_track([(0, 0, 0), (100, 0, 0)])                            # pushes the config; the client acks with its map
    check(any(l.endswith("map: west_coast_usa (from client Darren)") for l in server_log[ms:]), "v0.29 map: learnt from the client's ack (%s)" % [l for l in server_log[ms:] if "map:" in l][:2])
    console("lap set maxLaps 0")
    jbeams[100] = "etk800"
    for k in list(M.carNames.keys()): M.carNames[k] = None
    drive(100, [(0, 0, 0)]); race_start([100]); drive(100, [(50, 0, 0)])
    drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)]); lapA = laps()[-1]
    meta = json.load(open("Resources/Server/kkLap/times.json")).get("_meta", {})
    check(meta.get("map") == "west_coast_usa" and meta.get("version") == "0.29" and meta.get("checkpoints") == 2 and meta.get("maxLaps") == 0 and isinstance(meta.get("updated"), int),
          "v0.29 _meta: after a lap, times.json says version, map, checkpoints, maxLaps and when (%s)" % meta)
    e = board()["id:42"]; b = e["bests"].get("west_coast_usa|ETK 800-Series", {})
    check(e["history"][0]["map"] == "west_coast_usa" and e["history"][0]["car"] == "ETK 800-Series" and b.get("best", {}).get("ms") == lapA["lapMs"] and b["laps"] >= 1 and b["standing"]["ms"] == lapA["lapMs"],
          "v0.29 bests: the lap is in history with its map and car, and is the best of west_coast_usa|ETK 800-Series (%s)" % {k: v for k, v in b.items() if k != "best"} if b else "no bests entry")
    jbeams[100] = "pickup"                                           # the player swaps car: BeamMP keeps vid 100
    mc = len(client_log); X0 = len(events_to_server)
    drive(100, [(50, 0, 0), (0, 0, 0)])                              # out of the bubble, back in
    cars = [json.loads(d).get("car") for n, d in events_to_server[X0:] if n == "kkLap_bubble" and json.loads(d).get("inside")]
    check(cars[-1:] == ["pickup"] and any("vid 100 changed car: ETK 800-Series -> pickup" in l for l in client_log[mc:]),
          "v0.29 car: a swap on the same vid is named afresh on the next bubble entry (%s)" % cars)
    race_start([100]); drive(100, [(50, 0, 0)])
    drive(100, [(100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)]); lapB = laps()[-1]
    e = board()["id:42"]
    check(lapB["car"] == "pickup" and e["history"][0]["car"] == "pickup" and "west_coast_usa|pickup" in e["bests"] and e["bests"]["west_coast_usa|ETK 800-Series"]["best"]["ms"] == lapA["lapMs"],
          "v0.29 bests: the next lap goes to the new car, the old car keeps its best (%s)" % sorted(e["bests"]))
    # a server restart on a BeamMP build that has MP.Get: the server's own map wins, a client cannot change it
    G.MP.Get = lambda i: "/levels/utah/info.json" if i == 4 else None
    G.MP.Settings = S.table(Map=4)
    ms = len(server_log)
    S.execute(SERVER.read_text()); G.onInit()
    check(any(l.endswith("map: utah (from server)") for l in server_log[ms:]), "v0.29 map: MP.Get(MP.Settings.Map) is asked first at load")
    r = console("lap times")
    check("_meta" not in r and "1. " in r, "v0.29 _meta: read back from times.json, it is not a driver (%r)" % r[:120])
    ms = len(server_log)
    fresh_track([(0, 0, 0), (100, 0, 0)])
    check(not any("map: west_coast_usa" in l for l in server_log[ms:]) and json.load(open("Resources/Server/kkLap/times.json"))["_meta"]["map"] == "utah",
          "v0.29 map: a client reporting another map does not override the server's")
    # the live board is v0.27-shaped (no history, no bests): the first v0.29 load stamps it with the server's map
    old = {"KiwiPower": {"name": "KiwiPower", "bestMs": 120719, "laps": 6, "car": "Porsche 911 (992)",
                         "standing": {"ms": 122429, "sectors": [1, 2], "kph": 214, "resets": 0, "car": "Porsche 911 (992)"},
                         "rolling": {"ms": 120719, "sectors": [1, 2], "kph": 213, "resets": 0, "car": "Porsche 911 (992)"}},
           "guest1": {"name": "guest1", "bestMs": 150000, "laps": 1, "legacy": {"ms": 150000, "sectors": [], "kph": 0, "resets": 0}}}
    json.dump(old, open("Resources/Server/kkLap/times.json", "w"))
    ms = len(server_log)
    S.execute(SERVER.read_text()); G.onInit()
    t = json.load(open("Resources/Server/kkLap/times.json"))
    kb = t["KiwiPower"].get("bests", {}).get("utah|Porsche 911 (992)", {}); gb = t["guest1"].get("bests", {}).get("utah|Unknown car", {})
    check(kb.get("best", {}).get("ms") == 120719 and kb["rolling"]["ms"] == 120719 and kb["standing"]["ms"] == 122429 and kb["laps"] == 6
          and gb.get("best", {}).get("ms") == 150000 and "standing" not in gb and t["_meta"]["map"] == "utah"
          and any("board: 2 entries from before v0.29 given the map utah" in l for l in server_log[ms:]),
          "v0.29 stamp: a v0.27 board gets bests on the server's map at the first load, legacy laps as best only, logged (%s | %s)" % (kb, gb))
    console("lap set maxLaps 99")
guard("v0.29", s_v29)

def s_v27_shadow():
    fresh_track([(0, 0, 0), (100, 0, 0)])
    positions[100] = (0, 0, 0); velocities[100] = (0.0, 0.0, 0.0)
    for _ in range(3): tick()
    def shadow_pairs(n0): return [(SHADOWS[i]["text"], DRAWN[SHADOWS[i]["at"]], COLOURS[SHADOWS[i]["at"]], SHADOWS[i]["cursor"]) for i in range(n0 + 1, len(SHADOWS) + 1)]
    n0 = len(SHADOWS); tick()
    pairs = shadow_pairs(n0)
    check(("x3 0:00.000", "x3 0:00.000", "1.00,0.75,0.10", "12,22") in pairs and HUD["shadow"] == True,
          "v0.27 shadow: the clock is drawn once in black at cursor+(2,2), then in amber on top (%s)" % pairs[:1])
    M.onHorn(100); n0 = len(SHADOWS); tick()
    pairs = shadow_pairs(n0)
    check(("x3 5", "x3 5", "1.00,0.90,0.30", "12,22") in pairs, "v0.27 shadow: the countdown digit has its shadow too (%s)" % pairs[:1])
    for _ in range(120): tick()
    C.globals().ui_imgui["noCursor"] = True; HUD["shadow"] = None; mc = len(client_log)
    n0 = len(SHADOWS); m0 = len(DRAWN)
    for _ in range(3): tick()
    check(len(SHADOWS) == n0 and HUD["shadow"] == False and not HUD["broken"] and sum("HUD shadow: cursor calls missing, no shadow" in l for l in client_log[mc:]) == 1
          and any(d == "x3 0:00.000" for d in list(DRAWN.values())[m0:]),
          "v0.27 shadow: no GetCursorPos in the binding: drawn plain, said once, HUD not broken")
    C.globals().ui_imgui["noCursor"] = False; HUD["shadow"] = None
    velocities[100] = (27.78, 0.0, 0.0)
guard("v0.27 shadow", s_v27_shadow)

def s_v27_names():
    b = board()
    b["guest6391187"] = {"bestMs": 150000, "laps": 1, "kph": 80, "resets": 0, "sectors": [70000, 80000]}   # live times.json, 27/10: no name field
    b["id:999"] = {"bestMs": 160000, "laps": 1, "kph": 80, "resets": 0, "sectors": [80000, 80000]}
    pathlib.Path("Resources/Server/kkLap/times.json").write_text(json.dumps(b))
    ms = len(server_log)
    S.execute(SERVER.read_text()); G.onInit()                       # a server (re)start reads the board
    nb = board()
    check(nb["guest6391187"]["name"] == "guest6391187" and any("board: entry guest6391187 had no name, the key is the name" in l for l in server_log[ms:]),
          "v0.27 names: a nameless guest entry loads and is saved with name = key")
    check(nb["id:999"]["name"] == "?" and sum("board: entry id:999 has no name yet" in l for l in server_log[ms:]) == 1, "v0.27 names: a nameless id: entry stays ? and is logged once")
    check(nb["guest6391187"]["legacy"]["ms"] == 150000 and nb["guest6391187"]["bestMs"] == 150000 and nb["guest6391187"].get("bestStandingMs") is None,
          "v0.27 names: a pre-0.27 bestMs is kept as a legacy lap, no standing or rolling invented for it")
    r = console("lap times")
    check("guest6391187  2:30.000" in r and "standing -  rolling -" in r, "v0.27 names: lap times shows the guest by its key with dashes for the two lap types (%r)" % r[:120])
    for _ in range(3): tick()
guard("v0.27 names", s_v27_names)
check(not any(p == -1 and n in ("kkLap_count", "kkLap_go", "kkLap_hud") and '"cancel"' not in d for p, n, d in events_to_client),
      "v0.27: over the whole bench no countdown, GO or race-start HUD line was sent to everyone (-1), only to the racers")
check(not any(p == -1 and ("race start in" in m or m.endswith("GO!") or m in ("[LAP] 4", "[LAP] 3", "[LAP] 2", "[LAP] 1")) for p, m in chat_out),
      "v0.27: nor a chat countdown line")
check(not dropped, "no client event was dropped by the server (after the v0.27 scenarios)")

print()
print("bench dir:", work)
print("RESULT:", "FAIL (%d)" % len(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
