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
C.execute("""
Engine = { Audio = { fail = 0, plays = {},
    createSource = function(ch, path) return nil end,
    deleteSource = function(id) end,
    playOnce = function(ch, path, opts)
        if Engine.Audio.fail > 0 then Engine.Audio.fail = Engine.Audio.fail - 1; error('stub: audio not ready') end
        table.insert(Engine.Audio.plays, path)
    end } }
local drawn = {}
ui_imgui = { drawn = drawn, scale = 1, colours = {}, failBegin = nil,   -- colours[i] = 'r,g,b' of drawn[i] (TextColored)
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
    Text = function(t) table.insert(drawn, 'x' .. ui_imgui.scale .. ' ' .. t) end,
    TextColored = function(c, t)
        table.insert(drawn, 'x' .. ui_imgui.scale .. ' ' .. t)
        ui_imgui.colours[#drawn] = string.format('%.2f,%.2f,%.2f', c[1], c[2], c[3])
    end,
    TextWrapped = function(t) end, Separator = function() end, Button = function() return false end,
}
""")
AUDIO = C.globals().Engine["Audio"]
DRAWN = C.globals().ui_imgui["drawn"]
COLOURS = C.globals().ui_imgui["colours"]

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

failures = []
def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond: failures.append(what)

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
drive(100, [(0, 0, 0)])                          # enter cp1: timing starts
t_start = clock_ms[0]
drive(100, [(50, 0, 0), (100, 0, 0)])            # leave, enter cp2 (split)
drive(100, [(50, 0, 0)] * 48)                    # cruise ~4.8 s so the lap clears minLapMs
drive(100, [(0, 0, 0)])                          # enter cp1 again: lap
expected = clock_ms[0] - t_start
got = laps()
check(len(got) == before + 1, "one lap event after crossing cp1 -> cp2 -> cp1")
if got:
    lap = got[-1]
    check(lap["vid"] == 100 and lap["lapMs"] == (expected if not SABOTAGE else -1),
          "lap time equals server clock between the two cp1 crossings (%d ms)" % expected)
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
check(not any(f and any(d.startswith("x2 ") for d in texts(f)) for f in nxt), "first split (no reference yet) shows no gap line")
check("TIMING STARTED" in flashes, "timing started flashed")
check(realtime[0] == "", "the Race Realtime Display app clock stays empty while the imgui timer runs (never two clocks)")
check(any(pid == -1 and "Darren  lap 1" in m for pid, m in chat_out), "lap announced to everyone in chat")
check(pathlib.Path("Resources/Server/kkLap/laps.log").exists(), "laps.log written")
mark = len(chat_out); chat("/lap times")
check(any("no times on the board yet" in m for _, m in chat_out[mark:]), "one lap is not enough for the board (minLaps 2)")
check(any("one more lap and your best counts" in m for _, m in chat_out), "driver is told the minimum")
# second lap with one physics reset in it: counted, allowed
drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 20)
M.onVehReset(100)                                              # Insert: car did not move -> physics reset
check(any("physics reset #1 counted" in l for l in client_log), "physics reset in place is counted, lap goes on")
drive(100, [(50, 0, 0)] * 30 + [(0, 0, 0)])
lap2 = laps()[-1]
check(lap2["laps"] == 2 and lap2["resets"] == 1, "lap 2 counts and carries resets=1")
check(lap2.get("deltaMs") == lap2["lapMs"] - 5100, "lap 2 carries the delta to the previous best (+%d ms)" % (lap2["lapMs"] - 5100))
splits = [json.loads(d) for (pid, n, d) in events_to_client if n == "kkLap_split"]
check(splits[0].get("deltaMs") is None and splits[1].get("deltaMs") is not None, "first sector has no delta, the second time through it does")
after1 = [f for t, f in timer_frames if t > t_lap1]
check(after1 and texts(after1[0])[0] == "x3 LAP  0:05.100" and texts(after1[0])[-1] == "x1 best 0:05.100",
      "after lap 1 the timer window shows 'LAP  0:05.100' big and the best line appears")
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
mark_s = len(server_log); mark_c = len(client_log)
drive(100, [(50, 0, 0), (0, 0, 0)])
t_restart = clock_ms[0]
check(any("partial lap dropped, restart" in l for l in client_log[mark_c:]) and any("restart pid=1" in l for l in server_log[mark_s:]),
      "driving back into the start bubble mid-lap restarts the lap on both sides")
before = len(laps())
drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 50 + [(0, 0, 0)])
got = laps()[before:]
check(len(got) == 1 and got[0]["lapMs"] == clock_ms[0] - t_restart, "the lap after a restart is timed from the restart, not from the first crossing")
# honk works from that state too (mid-lap, back in the bubble)
for _ in range(200): tick()
mark = len(chat_out); positions[100] = (0, 0, 0); velocities[100] = (0.0, 0.0, 0.0); tick(); M.onHorn(100)
check(any(pid == -1 and "race start in" in m for pid, m in chat_out[mark:]), "honk in the bubble arms a countdown whatever state the car was in")
for _ in range(130): tick()
velocities[100] = (27.78, 0.0, 0.0)

# ------------------------------------------------------------------ scenario 2d: crossing the start backwards is ignored
mark_c = len(client_log); mark_e = len(events_to_client)
positions[100] = (50, 0, 0); tick()
velocities[100] = (-27.78, 0.0, 0.0)                           # driving back along -x into the start (heading was +x)
drive(100, [(0, 0, 0)])
check(any("backwards, ignored" in l for l in client_log[mark_c:]), "entering the start against its heading says WRONG WAY and sends nothing")
check(not any(n == "kkLap_info" and "timing started" in d for (p, n, d) in events_to_client[mark_e:]), "no timing started from a backwards crossing")
velocities[100] = (27.78, 0.0, 0.0); positions[100] = (50, 0, 0); tick()

# ------------------------------------------------------------------ scenario 3: second vehicle on the same client times separately
# vid 101 jumps sphere to sphere with NO frame outside in between. That is the case
# the first bench run caught: the inside flag leaked across checkpoints.
positions[100] = (50, 0, 0)
drive(101, [(0, 0, 0)]); drive(101, [(100, 0, 0)]); drive(101, [(50, 0, 0)] * 70); drive(101, [(0, 0, 0)])
got = laps()
check(got[-1]["vid"] == 101 and got[-1]["laps"] == 1, "vid 101 gets its own lap count (split-screen for free)")

# ------------------------------------------------------------------ scenario 4: too-short lap is rejected
chat("/lap reset")                               # server state AND client state back to cp1
positions[100] = (50, 0, 0); tick()
n = len(laps()); mark = len(server_log)
drive(100, [(0, 0, 0), (50, 0, 0), (100, 0, 0), (50, 0, 0), (0, 0, 0)])   # full lap in 400 ms
check(len(laps()) == n, "lap under minLapMs is rejected")
check(any("lap rejected" in l for l in server_log[mark:]), "server log says why it was rejected")

# ------------------------------------------------------------------ scenario 5: /lap clear wipes state on both sides
chat("/lap clear")
mark = len(client_log)
drive(100, [(50, 0, 0), (0, 0, 0)])
check(not any("entered cp" in l for l in client_log[mark:]), "after /lap clear the client reports no crossings")
check(any("[KKLAP][C1]" in l for l in server_log), "client debug lines reach the server log with the [C<pid>] tag")
check((work / "Resources/Server/kkLap/kkLap.log").exists(), "kkLap.log file is written")
check(not dropped, "no client event was dropped by the server for an unknown name (first: %s)" % (dropped[:1] or "-"))

# ------------------------------------------------------------------ scenario 7: two cars sit on the line -> countdown -> GO -> both laps count from GO
positions[100] = (0, 0, 0); chat("/lap mark"); M.onSurface(100, True, "FL=ASPHALT")
positions[100] = (100, 0, 0); chat("/lap mark 10"); M.onSurface(100, True, "FL=ASPHALT")   # track again after /lap clear
chat("/lap reset")
positions[100] = (0, 0, 0); positions[101] = (2, 0, 0)        # both parked in the start bubble
velocities[100] = (0.0, 0.0, 0.0); velocities[101] = (0.0, 0.0, 0.0)
mark_chat = len(chat_out); mark_cl = len(client_log)
for _ in range(10): tick()
M.onHorn(100)                                                 # vehicle Lua reports the horn; car 100 is on the line
check(any("honked on the line" in l for l in client_log[mark_cl:]), "a honk inside the start bubble asks for a countdown")
check(any(pid == -1 and "ready  (1 of 2 in the start bubble" in m for pid, m in chat_out[mark_chat:]), "with two cars in the bubble one honk is not enough: 1 of 2 ready")
check(any(n == "kkLap_hud" for (_, n, _) in events_to_client) and any(t.startswith("Darren ready  (1 of 2") and not big for t, big in hud_items()),
      "the 'ready 1 of 2' line reaches the on-screen HUD (kkLap_hud event lands in the queue)")
check(not any("race start in" in m for _, m in chat_out[mark_chat:]), "no countdown until everyone in the bubble has honked")
M.onHorn(101)                                                 # second car honks: everyone ready
check(any(pid == -1 and "race start in 5" in m and "all 2 ready" in m for pid, m in chat_out[mark_chat:]), "second honk starts the countdown for the group")
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
check(any(pid == -1 and m.endswith("GO!") for pid, m in chat_out[mark_chat:]), "GO announced to everyone")
check(flashes[-6:] == ["5", "4", "3", "2", "1", "GO!"], "countdown flashed big 5..1 GO (%s)" % flashes[-6:])
check(any("vid 100 GO start" in l for l in client_log[mark_cl:]) and any("vid 101 GO start" in l for l in client_log[mark_cl:]),
      "both parked cars take the GO start")
gos = [json.loads(d) for (pid, n, d) in events_to_client if n == "kkLap_go"]
t_go = gos[-1]["t"]
for _ in range(20): tick()                                    # sit 1 s after GO: must NOT re-arm
for _ in range(100): tick()                                   # 5 s more: every HUD item has run out
check(not any(t in ("5", "4", "3", "2", "1", "GO!") for t, _ in hud_items()), "HUD items expire after their ttl (%s)" % hud_items())
check(sum(1 for pid, m in chat_out[mark_chat:] if pid == -1 and "race start in" in m) == 1, "no second countdown from cars still parked after GO")
velocities[100] = (27.78, 0.0, 0.0); velocities[101] = (27.78, 0.0, 0.0)
before = len(laps())
drive(100, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 40 + [(0, 0, 0)])     # vid 100 lap
t100 = clock_ms[0]
drive(101, [(50, 0, 0), (100, 0, 0)] + [(50, 0, 0)] * 10 + [(0, 0, 0)])     # vid 101 lap, slower
t101 = clock_ms[0]
got = laps()[before:]
check(len(got) == 2, "both cars finish a lap after the GO start")
if len(got) == 2:
    check(got[0]["vid"] == 100 and got[0]["lapMs"] == t100 - t_go, "vid 100 lap counted from the GO instant, not from its crossing")
    check(got[1]["vid"] == 101 and got[1]["lapMs"] == t101 - t_go, "vid 101 lap counted from the same GO instant")
race_tags = [re.search(r"race (\d+)$", m).group(1) for pid, m in chat_out[mark_chat:] if pid == -1 and "  lap " in m and re.search(r"race (\d+)$", m)]
check(len(race_tags) == 2 and race_tags[0] == race_tags[1], "both laps carry the same race number so they compare together (%s)" % race_tags)
# jump start: park, arm, drive off during the countdown
for _ in range(200): tick()                                   # 10 s cooldown after GO
chat("/lap reset"); positions[100] = (0, 0, 0); positions[101] = (300, 300, 0); velocities[100] = (0.0, 0.0, 0.0)
for _ in range(10): tick(); M.onHorn(100)                      # honk: countdown starts
mark_chat = len(chat_out)
for _ in range(10): tick()
velocities[100] = (10.0, 0.0, 0.0); positions[100] = (20, 0, 0)   # drives off the line mid-countdown
for _ in range(5): tick()
check(any(pid == -1 and "jumped the start" in m for pid, m in chat_out[mark_chat:]), "driving off during the countdown is announced as a jump start")
check(any("jumped the start" in t for t, _ in hud_items()), "the jump start reaches the HUD too")
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
before = len(laps())
drive(100, [(5, 0, 0)]); t_s = clock_ms[0]                     # start: timing starts
drive(100, [(50, 0, 0), (100, 0, 0)] + [(150, 0, 0)] * 55)     # cp2 split, cruise past minLapMs
drive(100, [(200, 0, 0)]); t_f = clock_ms[0]                   # finish: run ends
got = laps()[before:]
check(len(got) == 1 and got[0]["lapMs"] == t_f - t_s, "point-to-point run timed from the start line to the finish line")
drive(100, [(150, 0, 0), (50, 0, 0), (5, 0, 0)])               # back through the start: starts again, no lap
check(len(laps()) == before + 1, "crossing the start after a finish starts a new run, counts nothing")
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
def countdown_since(mark): return [m for pid, m in chat_out[mark:] if pid == -1 and "race start in" in m]
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
check(any(pid == -1 and "honked twice: starting without the rest" in m for pid, m in chat_out[mark:]) and len(countdown_since(mark)) == 1,
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
chat("/lap reset")
for _ in range(5): tick()
check(all(f is None for _, f in timer_frames[-5:]), "timer: no window while no own car is timing")
hud_seen.clear(); mark_t = len(toasts); mark_f = len(flashes)
positions[100] = (5, 0, 0); tick(); t0 = clock_ms[0]          # into the start: timing started
positions[100] = (50, 0, 0)
for _ in range(20): tick()
fr = tframes(t0, clock_ms[0])
check(fr and all(f and RUN.match(texts(f)[0]) and len(f) == 1 for f in fr), "timer: running clock big, alone, from the frame after timing starts")
# lap A: first sector 1 s, no reference yet after /lap reset: no gap line at all
positions[100] = (100, 0, 0); tick(); t_sp = clock_ms[0]
spA = json.loads([d for (p, n, d) in events_to_client if n == "kkLap_split"][-1])
positions[100] = (50, 0, 0)
for _ in range(100): tick()
check(spA.get("deltaMs") is None and not any(f and any(d.startswith("x2 ") for d in texts(f)) for f in tframes(t_sp, clock_ms[0])),
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
check(all(f and texts(f)[-1] == "x1 best " + fmt(lp["bestMs"]) for f in tframes(t_lap, clock_ms[0])),
      "timer: the small best line appears once a lap is set and stays")
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
    check(off and all(f and not any(d.startswith("x2 ") for d in texts(f)) for f in off), "timer: then the gap line is gone")
else:
    check(False, "timer: lap C sector should be slower than lap B's (%s)" % spC.get("deltaMs"))
check(lpB["laps"] == 2 and texts(timer_frames[-1][1])[-1] == "x1 best " + fmt(lpB["bestMs"]), "timer: best line follows the server's best for this car")
check(not any(t.startswith("S1") or "lap " in t for t in toasts[mark_t:]) and not any(f.startswith("S1") or f.startswith("LAP") for f in flashes[mark_f:])
      and not any(t.startswith("S1") or t.startswith("LAP") or "last sector" in t for t in hud_seen),
      "timer: no toast, flash or HUD-queue item for splits or laps (%s)" % sorted(hud_seen))
# two own cars timing: the window follows the car the camera is on (be:getPlayerVehicle(0))
positions[101] = (50, 0, 0); tick(); positions[101] = (5, 0, 0); tick(); positions[101] = (50, 0, 0); tick()
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
positions[100] = (5, 0, 0); tick(); positions[100] = (50, 0, 0)
for _ in range(10): tick()
check(all(f is None for _, f in timer_frames[-10:]) and re.match(r"^\d+:\d\d\.\d{3}$", realtime[0] or ""),
      "timer: /hud off hides the window and the running clock goes to the UI app instead")
chat("/hud on")
for _ in range(3): tick()

print()
print("bench dir:", work)
print("RESULT:", "FAIL (%d)" % len(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
