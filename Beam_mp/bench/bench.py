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
    RegisterEvent=s_register, CreateTimer=s_timer, TriggerClientEventJson=s_trigger_client_json,
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
    def getVelocityXYZ(self):
        return velocities.get(self.vid, (0.0, 0.0, 0.0))    # m/s, three values like the game

C.globals().TriggerServerEvent = c_trigger_server
C.globals().AddEventHandler    = c_add_handler
C.globals().MPVehicleGE = C.table(getOwnMap=lambda: C.table_from({v: C.table() for v in own_vids}))
C.globals().be = C.table(
    getObjectByID=lambda self, vid: Veh(vid) if vid in positions else None,
    getPlayerVehicle=lambda self, seat: Veh(seat0_vid[0]) if seat0_vid[0] else None,
)
C.globals().guihooks = C.table(trigger=lambda name, t: toasts.append(t["msg"]))
C.globals().log = lambda lvl, origin, msg: client_log.append("[%s] %s" % (lvl, msg))
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
def tick(dt_s=0.05, ms=None):
    """advance the clock and run one client frame"""
    clock_ms[0] += int((ms if ms is not None else dt_s*1000))
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
for _ in range(120): tick()                      # 6 s of frames, no checkpoints yet
check(any("alive, own vehicles: 100,101" in l for l in client_log), "alive line fires with zero checkpoints")

# ------------------------------------------------------------------ scenario 1: mark a track from the car
own_vids[:] = [100, 101]; seat0_vid[0] = 100
positions[100] = (0, 0, 0);   positions[101] = (500, 500, 0)
chat("/lap mark")                                # cp1 at car (0,0,0)
positions[100] = (100, 0, 0); chat("/lap mark 10")   # cp2 at (100,0,0) r=10
cfgfile = pathlib.Path("Resources/Server/kkLap/config.json")
check(cfgfile.exists(), "/lap mark round trip wrote config.json")
cfg = json.load(open(cfgfile)) if cfgfile.exists() else {"checkpoints": [], "defaultRadius": None}
if not cfg["checkpoints"]: cfg["checkpoints"] = [{"r": None}, {"r": None}]
check(len(cfg["checkpoints"]) == 2, "two checkpoints saved to config.json")
check(cfg["checkpoints"][1]["r"] == 10, "/lap mark 10 sets radius 10")
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
check(any(pid == -1 and "Darren  lap 1" in m for pid, m in chat_out), "lap announced to everyone in chat")
check(pathlib.Path("Resources/Server/kkLap/laps.log").exists(), "laps.log written")
check(pathlib.Path("Resources/Server/kkLap/times.json").exists(), "times.json written")
mark = len(chat_out); chat("/lap times")
check(any("1. Darren" in m for _, m in chat_out[mark:]), "/lap times lists the best lap")

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

# ------------------------------------------------------------------ scenario 6: /lap markers off reaches every client
mark = len(server_log)
chat("/lap markers off")
check(any("markers=false" in l for l in server_log[mark:]), "/lap markers off is pushed to the client config")
check(any("markers false" in m for _, m in chat_out), "chat confirms markers off")

print()
print("bench dir:", work)
print("RESULT:", "FAIL (%d)" % len(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
