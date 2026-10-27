# kkLap - BeamMP lap timer that works with 0.40 split-screen (Linux server)

Server plugin + client mod. Server keeps the clock and the checkpoints; clients only
report "my vehicle entered checkpoint N". Split-screen is one client with two vehicles,
so every message carries the vehicle id.

## Layout (drop straight onto the BeamMP server)
Client zip holds three files: scripts/kkLap/modScript.lua, lua/ge/extensions/kkLap.lua (game side)
and lua/vehicle/extensions/kkLap/kkLapVE.lua (inside each car, horn only).
    Resources/Server/kkLap/main.lua                      server plugin
    Resources/Server/kkLap/config.json                   written by the plugin, edit live via /lap
    Resources/Client/kkLap/scripts/kkLap/modScript.lua    client mod source
    Resources/Client/kkLap/lua/ge/extensions/kkLap.lua
    Resources/Client/kkLap_v<VERSION>.zip                 built by tools/build_client_zip.py

Linux is case-sensitive. `Resources`, `Server`, `Client`, `kkLap` exactly as above, or the
server loads nothing and says nothing.

## Build the client zip
Bump M.VERSION in kkLap.lua and the modScript log line first. The zip takes its name from it.
When uploading a new version, delete the old kkLap_v*.zip from the server, or players get both.
    python3 Beam_mp/tools/build_client_zip.py

## Bench test before uploading (no server needed)
    pip install lupa
    python3 Beam_mp/bench/bench.py
Runs the real server and client Lua against each other with fake BeamMP calls and a fake car.

## In game
    /lap mark          drop a checkpoint at your car. First one is start/finish.
    /lap list          show them
    /lap undo          remove the last one
    /lap clear         remove all
    /lap reset         wipe timing, keep checkpoints
    /lap race [sec]    countdown for everyone, GO starts every car in the start bubble (v0.6.0)
    /lap autostart on|off  a honk inside the start bubble starts that countdown (default on)
    /lap set countdown 5
    /lap line paint|draw  start/finish line: painted on the road, or flat drawn chequer (v0.8.0)
    /lap set lineWidth 10   metres across the road
    /lap times         best lap per player with sector times, top 10 (v0.5.0)
    /lap announce on|off  every sector to everyone in chat (default off; laps always announce)
    /lap markers on|off  draw the checkpoint spheres in the world (default on, v0.4.0)
    /lap debug on|off  forward client debug lines to the server log
    /lap set minLapMs 5000
    /lap set markerAlpha 40   see-through-ness of the markers, 0 invisible, 255 solid (v0.5.2)

## Debug: read it slowly
The console scrolls too fast. The plugin appends every line to
    Resources/Server/kkLap/kkLap.log
Open that in the Pterodactyl file manager, or in the console:  tail -f Resources/Server/kkLap/kkLap.log
With `/lap debug on`, client-side lines arrive there too, tagged [KKLAP][C<pid>],
so one file tells the whole story. Game side, if ever needed: grep KKLAP beamng.log

## Rule learned the hard way
Never put a colon in an event name. BeamMP splits `name:data` at the first colon on both
server and client, so `kkLap:cfg` becomes event `kkLap` with data `cfg...` and is dropped silently.
Names here are `kkLap_cfg`, `kkLap_cross`, and so on.

## The start/finish line (v0.8.0)
The first /lap mark is special. It is only accepted when every wheel touching the ground is on
tarseal (ASPHALT, ASPHALT_WET, COBBLESTONE, RUMBLE_STRIP), read inside the car's own Lua the
way the game's own sound code does; on grass you get "start/finish must be on tarseal.
Wheels are on: FL=GRASS,...". It also records which way the car was pointing, and the line is
laid ACROSS the road from that, lineWidth metres wide.
Two looks: "paint" (default) creates a real road decal with the game's chequered start-line
material. UNCHECKED on every map: if it shows as an orange warning texture the material is not
on that map, type /lap line draw. "draw" is a flat black-and-white chequer, 2 cm high, drawn
every frame with the same tools as the spheres. Neither changes grip or the AI map.
Research for this (sources, confidence per call) is in STATE.md and the bench covers the
surface read in the car's own VM.

## Big writing on screen (v0.7.0)
Countdown, GO, every sector time and your own lap time flash BIG in the centre of the
screen, with the game's own countdown beeps. A running lap clock sits mid-screen (a
client-side estimate; the official time is the server's and comes with the lap toast).
Both need two stock BeamNG UI apps on your layout, one time per player: press Alt+U,
add "Race Countdown" and "Race Realtime Display", Alt+U again to leave. (App names from
docs.beammp.com/beamng/lua-snippets; UNCHECKED whether a fresh freeroam layout already has
them.) Without them nothing breaks: the small top-left toasts still carry everything.

## Racing together (v0.6.0)
Everyone parks in the start bubble. Anyone honks. Chat and screen count 5, 4, 3, 2, 1, GO.
At GO every car still inside the bubble starts its lap from the same server instant, so the
times compare. A car that was on the line and leaves the bubble before GO gets
"<name> jumped the start" in chat for all to see. Cars arriving later just cross the line as
usual. /lap race does the same countdown without a honk.
How the horn is heard: a tiny vehicle-side file (lua/vehicle/extensions/kkLap/kkLapVE.lua)
is loaded into every car when it spawns, the same way BeamMP loads its own; it watches
electrics.values.horn and calls kkLap.onHorn(id) on the game side once per press.

## What you get per lap (v0.5.0)
Each checkpoint shows the driver a toast: sector time (since the previous checkpoint), split
(since the line) and the speed entering the sphere, read straight off the car. Each finished
lap is announced to everyone in chat, with the sector list and the speed over the line:
    [LAP] Darren  lap 1  0:05.100  [0.2 | 4.9]  best 0:05.100  @100 km/h
The server writes two files beside main.lua: laps.log (one line per lap, append only, the
record) and times.json (best lap per player, what /lap times reads). /lap clear wipes
times.json for the new track; laps.log is never deleted.

## What you see (v0.4.0)
Every checkpoint is drawn as the exact sphere the timer uses, label floating above it:
start/finish green (it never fades),
the rest orange, and the one YOUR car has to hit next in blue.
v0.5.1: the one you just went through flashes white for 1.5 s with "OK" on the label, then
goes grey with "done" until you cross the line again. Grey behind you, blue ahead.
What you see is what triggers, so a wrong radius shows up before a wrong lap does.
Drawn with the same two FFI calls BeamMP uses for its own name tags. If they are missing
the markers vanish and the timer still runs; kkLap.log says "markers NOT available".

## What is BeamMP and what is BeamNG in the client file
BeamMP: TriggerServerEvent, AddEventHandler, MPVehicleGE.getOwnMap.
BeamNG game Lua: be:getObjectByID, be:getPlayerVehicle, veh:getPosition, veh:getVelocityXYZ, veh:getID, guihooks.trigger,
log, jsonEncode/jsonDecode, ffi.C.BNG_DBG_DRAW_Sphere, ffi.C.BNG_DBG_DRAW_TextAdvanced, color().
Server file is 100% BeamMP server API (MP.*, Util.Json*).
