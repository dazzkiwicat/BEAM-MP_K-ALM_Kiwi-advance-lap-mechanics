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

## Deltas, identity, wrong way (v0.20)
Every sector flash shows the gap to your own best for that sector, "S3 10.2 -0.3" (minus is
faster), and the lap flash shows the gap to your previous best. Bests persist on the board.
The board is keyed by BeamMP account id, so a guest whose name changes every session keeps
their record; the name shown is the latest one. Crossing the start line against the heading
it was marked with flashes WRONG WAY and starts nothing.

## Results into Discord (tools/discord_poster.py)
The BeamMP server cannot call out, so this script runs elsewhere (a scheduled task from the
cloud session, or any PC): it reads laps.log through the Pterodactyl API and posts the new
lines to a Discord webhook. Needs PTERO_URL, PTERO_API_KEY, PTERO_SERVER_ID, DISCORD_WEBHOOK
in the environment, never in chat or git. Dry run: --local <laps.log> --dry-run.

## Admin from the server console (v0.25)
Every command that changes the track or the settings runs from the BeamMP server console, not
from game chat. In the Pterodactyl panel: open the server, the **Console** tab, type at the prompt
at the bottom, Enter. Start the line with `lap` (`/lap` and `kklap` work too, any case):
    lap set countdown 5
    lap music set.mp3 0.4
    lap race stop
    lap admin list
    lap help           every admin command, in this spelling
The answer prints right under the line. Every console line is written to kkLap.log as
`[KKLAP][CONSOLE] <line>`, so every change leaves a record. Lines that are not kkLap's (BeamMP's own
`status`, `kick`, `say` ...) are left to the server.
The console has no car, so the three commands that need one take a player name: kkLap asks THAT
player's game where their car is, and the result goes to them in chat and to kkLap.log / the console:
    lap mark Darren [r]     lap start Darren [r]     lap finish Darren [r]
(any case; the start of a name is enough when only one player matches).

Chat admin commands are OFF by default: typed in chat, an admin command answers "admin commands
run from the server console only" and does nothing, for admins too. `lap adminchat on` (console
only) lets the admins in config.json (below) type them in chat again, as /mark, /set ...;
`lap adminchat off` stops it. Player commands in chat never change: /rules, /times, /help, /dj
(where the DJ is), honking.

The admin commands (console spelling; with adminchat on, the same words work in chat as /word):
    lap mark <player> [r]    drop a checkpoint at that player's car. First one is start/finish.
    lap start <player> [r]   move the start line to their car. Every other checkpoint is kept. (v0.09)
    lap finish <player> [r]  a separate finish line at their car: point-to-point course (hill climb, drag).
    lap finish same    finish is the start line again (loop). Default.
    lap list           show them
    lap undo           remove the last one
    lap clear          remove all
    lap reset          wipe timing, keep checkpoints
    lap race [sec]     countdown for everyone, GO starts every car in the start bubble (v0.6.0); sec 1..60
    lap race stop      call a running countdown off; the cars in the bubble honk again (v0.24)
    lap autostart on|off  a honk inside the start bubble starts that countdown (default on)
    lap set countdown 5   (1..60)
    lap line paint|draw  start/finish line: painted on the road, or flat drawn chequer (v0.8.0)
    lap set lineWidth 10   metres across the road (1..50)
    lap music <name> [0-10] [once|<sec>]  play art/sound/kkLap/<name>(.ogg, or name.mp3) on every client, on repeat; >1 boosts
    lap music stop     (music also stops by itself when you leave the level, v0.19)
    lap dj <text>      set where the Discord DJ set is; everyone sees it (v0.13)
    lap times          best lap per player with sector times, resets and points, top 10
    lap set minLaps 2        laps a driver needs before their best goes on the board (v0.10) (1..100)
    lap set pointsMinDrivers 3   drivers needed before points show (1..100)
    lap announce on|off  every sector to everyone in chat (default off; laps always announce)
    lap markers on|off  draw the checkpoint spheres in the world (default on, v0.4.0)
    lap hud on|off     kkLap's own on-screen text, no UI app needed (default on, v0.22)
    lap debug on|off   forward client debug lines to the server log
    lap set minLapMs 5000    (0..3600000)
    lap set markerAlpha 40   see-through-ness of the markers, 0 invisible, 255 solid (v0.5.2)
    lap set defaultRadius 8  (1..100)   lap set musicVolume 0.5  (0..10)
    lap rules | lap rules add <text> | lap rules clear
    lap admin add|remove <name> | lap admin list
    lap status         what the plugin sees right now (v0.25, see "When something goes wrong")
    lap report         writes report.txt to upload (v0.25)
    lap adminchat on|off
The on|off commands with no word after them show the current value and change nothing (v0.24).
set takes only the keys above, in range; whole-number keys are rounded down; anything else is
refused with the range (v0.24: a countdown of 1e999 used to jam every race start).
Config pushes (any admin command) no longer reset anyone's lap (v0.24). The config carries a
track revision, trackRev, that moves only on mark, start, finish, finish same, undo, clear,
reset and a server (re)start; only then do the clients and the server start every lap afresh.

## Who may do what (v0.19, v0.25)
The server console can do everything, always. Players in chat: /rules (the in-game window), /times,
/help, /dj, and honking. With `lap adminchat on`, the admins may also use the admin commands in
chat. Admins are player names in config.json; while the list is empty everyone counts as admin
(only matters with adminchat on), and the first `lap admin add <name>` locks it.

## The rules window (v0.19)
A window inside the game (the game's own ImGui, same as BeamMP's debug windows) opens on
join and on /rules, with the rules text, the player commands, and (only while adminchat is on)
the admin commands if you are one. `lap rules add <text>` adds a rule line, `lap rules clear`
empties them; /rules on its own is for everyone. rulesOnJoin is not a number, so edit config.json
to turn the join pop-up off.

## In game (players)
Every command works with or without the lap word: /times is /lap times (v0.12). Other plugins'
slash commands are left alone.
    /rules             open or close the rules window
    /times             best lap per player with sector times, resets and points, top 10
    /help              these commands
    /dj                where the Discord DJ set is
Honk in the start bubble to start (twice: start without the rest), see "Racing together".

## When something goes wrong (v0.25)
In the console: `lap report`. It writes Resources/Server/kkLap/report.txt: what `lap status`
shows, the whole running config, the board, and the last 300 lines of kkLap.log. Download that
one file from the panel's Files tab and upload it; nothing else is needed.
`lap status` prints the same live picture in the console: version, players and which client mod
version each runs, race state, who is in the start bubble (ready, waiting, started), each run
(next checkpoint, lap start, laps, invalid and why), and the key settings.
Every refusal in kkLap.log says why, with the numbers it looked at:
    arm Darren#100: 1 of 2 ready [Darren#100 ready, Bob#7 waiting]
    no countdown: waiting on Bob#7
A honk (arm) from Darren's car 100: of the 2 cars waiting in the start bubble, 1 has honked; the
list is everyone it counted. The countdown waits for Bob's car 7 (or Darren honks again). When it
starts, the line ends "-> countdown". "no countdown: GO was 3.2 s ago ..." = honks are ignored
for 10 s after a GO.
    bubble +Darren#100 (now 2) / bubble -Darren#100 (now 1)   a car entered / left the start bubble
    cross refused Darren#100: got cp 3, wanted cp 2            it skipped a checkpoint (radius?)
    lap rejected, too short: Darren#100 600 ms < minLapMs 5000
    LAP NOT COUNTED Darren#100 1:02.300 (invalidated: position reset, moved 30 m)   also in laps.log
    GO cross refused Darren#100: GO was 6200 ms ago (limit 5000); line start from now instead
    [W] client Bob runs v0.24, server is v0.25   Bob has an old zip cached; he is told to rejoin
    [C1] ERROR onUpdate error x1: ...            the game side hit an error; it keeps running and counts it
Client lines ([C<pid>], debug on) say when cars spawn, are deleted or pruned (and whether they were
in the bubble), why each config kept or reset the lap ("same track (rev 7): lap state kept"), every
music decision, and the screen size at the first HUD draw. The "alive" line is logged once a
minute per player, or at once when it changes.

## Debug: read it slowly
The console scrolls too fast. The plugin appends every line to
    Resources/Server/kkLap/kkLap.log
Open that in the Pterodactyl file manager, or in the console:  tail -f Resources/Server/kkLap/kkLap.log
With `lap debug on` (console), client-side lines arrive there too, tagged [KKLAP][C<pid>],
so one file tells the whole story. Game side, if ever needed: grep KKLAP beamng.log

## Rule learned the hard way
Never put a colon in an event name. BeamMP splits `name:data` at the first colon on both
server and client, so `kkLap:cfg` becomes event `kkLap` with data `cfg...` and is dropped silently.
Names here are `kkLap_cfg`, `kkLap_cross`, and so on.

## Music (v0.11, v0.14 loops, v0.21 autoplay)
v0.21: an admin's music <name> (console: lap music <name>) is remembered in config as the track for everyone. Each player
hears it with no command, joiners included. /music stop clears it.
v0.22: it starts as soon as the player's game gets the config, on joining, before they have a
car (v0.21 waited for a car and missed the first one, so it only began after a Ctrl+R). If the
game refuses to play yet, it tries again every 3 s, up to 10 times.
It plays through the "Other" slider in the game's Options > Audio (so do the countdown beeps).
If it is quiet, check that slider before anything else (Darren, 27/10).
The track does NOT go in the kkLap zip or in git. It lives in its own client zip on the
server, kkLap_music.zip, holding only art/sound/kkLap/<name>.ogg. BeamMP mounts every zip
in Resources/Client, so the game finds the file from either. Code zip changes every
version; the music zip is made once. Make it: zip a folder tree art/sound/kkLap/set.ogg.
The server plays nothing; it tells every client, and each game plays the file out of the mod
zip. Drop .ogg files in Resources/Client/kkLap/art/sound/kkLap/, rebuild the zip, upload.
Every joiner downloads the zip, so keep tracks small (96 kbps ogg, ~2 MB for 3 minutes;
ffmpeg line in that folder's README.txt). Calls are the ones RLS and BeamJoy (a BeamMP mod)
use: Engine.Audio.createSource / deleteSource, with playOnce as the fallback. Only ship
music you may distribute: the zip is distribution.

## Resets, the board and points (v0.10)
A physics reset (Insert, car stays put) is allowed and counted: it shows on the lap line
("resets 2") and on the board. Anything that MOVES the car mid-lap (recover, respawn,
teleport) makes that lap not count: everyone sees "<name> lap will not count: position
reset, moved 30 m" the moment it happens, and "<name> 1:02.3 not counted" at the line.
Judged in the car's own reset callback at 2 m, with a 150 m per-frame backstop for
teleports that fire no reset. A driver's best goes on the board only once they have
minLaps (2) laps in the run; then any of their laps can be the record.
Points: 100 for the top time, floor(100 * top / yours), so 10% slower is 90 points. They
only appear in /lap times once pointsMinDrivers (3) drivers have times.

## Start and finish (v0.09)
By default the start line is the finish line: a loop, and every crossing after the first
ends a lap. /lap finish puts a separate finish line at your car and the course becomes
point-to-point: the start only starts (crossing it mid-run restarts you), the finish ends
the run, then you go back to the start to go again. The finish gets its own chequered line
and its own tarseal check. /lap start moves the start line without touching the rest.
The honk countdown works the same either way: park in the start bubble, honk, GO.

## The start/finish line (v0.8.0)
The first /lap mark is special. It is only accepted when every wheel touching the ground is on
tarseal (ASPHALT, ASPHALT_WET, COBBLESTONE, RUMBLE_STRIP), read inside the car's own Lua the
way the game's own sound code does; on grass you get "start/finish must be on tarseal.
Wheels are on: FL=GRASS,...". It also records which way the car was pointing, and the line is
laid ACROSS the road from that, lineWidth metres wide.
Two looks: "paint" (default) creates a real road decal with the game's chequered start-line
material. UNCHECKED on every map: if it shows as an orange warning texture the material is not
on that map, type lap line draw in the console. "draw" is a flat black-and-white chequer, 2 cm high, drawn
every frame with the same tools as the spheres. Neither changes grip or the AI map.
Research for this (sources, confidence per call) is in STATE.md and the bench covers the
surface read in the car's own VM.

## kkLap's own on-screen text, the HUD (v0.22)
Works with no UI app at all, so it works in split screen where the countdown and chat apps
have no room. Top centre of each game window: the countdown digits, GO and WRONG WAY in big
letters, and under them up to 4 recent small lines: "timing started" and the race-start lines
from the server ("Darren ready (1 of 2 ...)", "race start in 5", "jumped the start", "honked
twice"). Lines fade out by themselves. It does not take clicks or the mouse.
Admin (console): lap hud off turns it off for everyone, lap hud on back.

## The race timer (v0.23)
Like the single-player time trials, right under the HUD text, top centre, no UI app needed:
- BIG: your running lap time. For 4 s after you cross the line it shows "LAP  1:23.456"
  (the server's official time), then the new lap's clock.
- MEDIUM: how far you are ahead or behind, for 3 s after each checkpoint and after the line:
  red "+0.35" = slower than your best, green "-0.21" = faster. Nothing on your first lap.
- SMALL: "best 1:22.900" once you have a lap.
It follows the car the camera is on (split screen: each own car keeps its own clock). It is
only there while your car is timing. The running clock is a client-side estimate; the lap time
it holds after the line is the server's.
The sector and lap toasts are gone: they were small and distracting. Laps are still in chat.

## Big writing on screen (v0.7.0)
Countdown and GO flash BIG in the centre of the screen, with the game's own countdown beeps,
if the stock "Race Countdown" UI app is on your layout (Alt+U, add it, Alt+U to leave; app name
from docs.beammp.com/beamng/lua-snippets). The kkLap HUD shows them anyway.
Fallback only: with /hud off (or if the game's imgui call fails) the lap clock goes back to the
"Race Realtime Display" UI app and the sector and lap times back to small top-left toasts.

## Racing together (v0.6.0)
The start bubble always works: drive back into it at any point, mid-lap, after a reset,
after a disqualified lap, and the partial lap is dropped and timing starts fresh. A honk
in there arms the countdown from any state too (v0.17).
Everyone parks in the start bubble and EVERY car in there honks once (chat says "Darren ready, 1 of 3").
When the last one honks, chat and screen count 5, 4, 3, 2, 1, GO. One car alone: its honk starts it.
Someone parked in the bubble and not honking? Honk a SECOND time and the countdown starts
without them ("Darren honked twice: starting without the rest"). They still get the GO start
if they are in the bubble at GO. A car that drives out, is deleted or respawned, or whose
player leaves, stops counting at once: if everyone left has honked, the countdown starts (v0.22).
Someone arriving after GO is the next group: their own honk, their own countdown. Laps from the
same GO carry the same race number in the chat line, so they compare together (v0.18).
At GO every car still inside the bubble starts its lap from the same server instant, so the
times compare. A car that was on the line and leaves the bubble before GO gets
"<name> jumped the start" in chat for all to see. Cars arriving later just cross the line as
usual. lap race (console) does the same countdown without a honk.
How the horn is heard: a tiny vehicle-side file (lua/vehicle/extensions/kkLap/kkLapVE.lua)
is loaded into every car when it spawns, the same way BeamMP loads its own; it watches
electrics.values.horn and calls kkLap.onHorn(id) on the game side once per press.

## What you get per lap (v0.5.0)
Each checkpoint shows the driver the gap to their best for that sector under the race timer
(v0.23; before that a toast with sector, split and speed, still the /hud off fallback). Each finished
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
log, jsonEncode/jsonDecode, ffi.C.BNG_DBG_DRAW_Sphere, ffi.C.BNG_DBG_DRAW_TextAdvanced, color(), ui_imgui (rules
window and HUD), and the onVehicleSpawned / onVehicleDestroyed hooks (BeamMP's MPVehicleGE.lua uses the same names).
Server file is 100% BeamMP server API (MP.*, Util.Json*).

## Web leaderboard
`leaderboard/index.html` reads `times.json` and shows rank, best lap, laps, speed at the line,
resets and points, refreshing every 30 seconds. `leaderboard/README.md` has the one Docker line
(Caddy, read-only mount of times.json) for whoever runs the host. Nothing writes back to the server.

## Licence
MIT, see LICENSE at the repo root. Use it, change it, ship it on your own server; keep the
copyright line. The music file is yours, not ours, and never lives in this repo.
