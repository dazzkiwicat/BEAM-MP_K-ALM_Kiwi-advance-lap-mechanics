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
def tick(dt_s=0.05, ms=None):
    """advance the clock and run one client frame"""
    clock_ms[0] += int((ms if ms is not None else dt_s*1000))
    fire_due_timers()
    M.onUpdate(dt_s)
    M.onPreRender(dt_s)                           # markers code runs every frame too (no ffi here: no-op draw)
def drive(vid, path, step_ms=100):
    """move vid through a list of points, one frame per point"""
    for p in path:
        positions[vid] = p; tick(ms=step_ms)
def laps(): return [json.loads(d) for (pid, n, d) in events_to_client if n == "kkLap_lap"]

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
check(any("S1" in t and "100 km/h" in t for t in toasts), "client showed the sector toast with speed")
check(any(f.startswith("S1") for f in flashes), "sector time flashed big in the centre")
check(any(f == "LAP  0:05.100" for f in flashes), "own lap time flashed big in the centre")
check("TIMING STARTED" in flashes, "timing started flashed")
check(realtime[0] == "LAP  0:05.100", "running clock shows the finished lap for a few seconds")
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
mark = len(chat_out); chat("/lap times")
check(any("1. Darren" in m and "resets 0" in m for _, m in chat_out[mark:]), "/lap times lists the best lap after 2 laps (lap 1, which had no resets)")
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
check(not any("race start in" in m for _, m in chat_out[mark_chat:]), "no countdown until everyone in the bubble has honked")
M.onHorn(101)                                                 # second car honks: everyone ready
check(any(pid == -1 and "race start in 5" in m and "all 2 ready" in m for pid, m in chat_out[mark_chat:]), "second honk starts the countdown for the group")
for _ in range(110): tick()                                   # 5.5 s: 4,3,2,1, GO
check(any(pid == -1 and m.endswith("GO!") for pid, m in chat_out[mark_chat:]), "GO announced to everyone")
check(flashes[-6:] == ["5", "4", "3", "2", "1", "GO!"], "countdown flashed big 5..1 GO (%s)" % flashes[-6:])
check(any("vid 100 GO start" in l for l in client_log[mark_cl:]) and any("vid 101 GO start" in l for l in client_log[mark_cl:]),
      "both parked cars take the GO start")
gos = [json.loads(d) for (pid, n, d) in events_to_client if n == "kkLap_go"]
t_go = gos[-1]["t"]
for _ in range(20): tick()                                    # sit 1 s after GO: must NOT re-arm
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
check(any("art/sound/kkLap/kiwi.ogg vol 0.30" in l for l in client_log[mark:]), "/lap music reaches the client with the file and volume (no audio engine here, so it reports the failure)")
check(any(pid == -1 and "music: kiwi" in m and "on repeat" in m for pid, m in chat_out), "music start announced to everyone, on repeat by default")
mark = len(client_log); chat("/music set.mp3 0.3")
check(any("art/sound/kkLap/set.mp3 vol 0.30" in l for l in client_log[mark:]), "a name with its own extension is used as given (mp3)")
mark = len(client_log); chat("/music set.mp3 3")
check(any("vol 3.00" in l for l in client_log[mark:]), "volume above 1 passes through as a boost")
mark = len(client_log); chat("/music kiwi 0.3 3")            # no audio engine here: timed loop re-fires every 3 s
for _ in range(70): tick()
check(sum(1 for l in client_log[mark:] if "music looped (timed)" in l) == 1, "timed loop re-fires after the given seconds")
mark = len(client_log); chat("/lap music stop")
check(any("music stopped" in l for l in client_log[mark:]), "/lap music stop reaches the client")

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

print()
print("bench dir:", work)
print("RESULT:", "FAIL (%d)" % len(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
