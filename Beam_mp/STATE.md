# kkLap STATE  (live file: read this first, update it last)

Updated: 2026-10-26 by Claude. Current version: 0.3.0 (server main.lua and client zip share it). Branch: claude/blissful-mccarthy-eg6jed

## Goal (Darren, chat 26/10)
A far better lap timer than the built-in one, built so later game modes (drag, pursuit,
derby...) bolt onto the same server/client plumbing. Split-screen is NOT the focus;
keying everything by vehicle id makes it work anyway at no cost, so that stays.

## Where we are
Skeleton written, syntax-checked, zipped, and **bench-tested end to end** (bench/bench.py:
real server Lua on 5.3 + real client Lua on LuaJIT, BeamMP calls stubbed and wired together,
fake car driven through the spheres). 14 checks pass; `--break` proves the bench can fail.
**Never run on a real server yet.** The bench models the plumbing; it cannot see whether
the game really calls onUpdate.

Bugs the bench caught on its first run (26/10), both fixed:
- client inside-flag leaked across checkpoints: touching spheres missed the second one.
- /lap reset cleared the server but not the clients; now it pushes config, which resets both.
- event names with a colon are cut at the colon by BeamMP on both ends (found live, 26/10).
  Rule: never put ':' in an event name. Data may contain colons, names may not.
Darren has previously had a server plugin load and print to the console (chat 26/10),
so the Resources/Server path and onInit are known to work on his setup.
Live status 26/10: server side loads and sees the join. **Client zip never ran on the PC**
(beamng.log has no KKLAP line from the mod). Suspect the upload in Resources/Client, see the
live test log. Next move: Darren's file listing of Resources/Client after the v0.3.0 upload.

## Ground truth (checked)
- BeamNG 0.40 (19 Oct 2026) has built-in split-screen + occupants. (web)
- BeamMP v4.23.1 supports 0.40. No split-screen or occupant hooks added. (web, GitHub releases)
- Split-screen joins a normal BeamMP server with 2 spawns allowed; the built-in
  game modes (lap timer, drag) do NOT work over BeamMP. (Darren, chat 26/10)
- Server API names in main.lua: all from docs.beammp.com latest-server-reference. (web)
- Client API names: TriggerServerEvent, AddEventHandler, MPVehicleGE.getOwnMap from
  docs.beammp.com Client-Side page; be:getObjectByID, be:getPlayerVehicle, getPosition,
  jsonEncode/Decode, guihooks.trigger('Message') all seen in BeamMP's own MPVehicleGE.lua. (web)
- Client mod zip layout scripts/modScript.lua + lua/ge/extensions/<name>.lua from the
  BeamMP getting-started guide. (web)

## Checked against BeamMP-Server source (cloned 26/10, TLuaEngine.cpp / TServer.cpp)
- onInit is registered by the server itself; no MP.RegisterEvent needed.
- onPlayerJoin is a real event the server fires. Client still asks for config on load.
- Plugin Lua state opens all standard libs (luaL_openlibs), so io.open and os.date work.
- onVehiclePaintChanged exists too (undocumented); irrelevant for now.

## UNCHECKED until the first live run
- Does the extension's onUpdate(dt) get called when loaded via modScript load()?
  The 5-second "alive" line in kkLap.log is the test. No line = hook not wired.
- be:getPlayerVehicle(0) is seat 0. In split-screen player 2 may be seat 1; /lap mark
  uses whoever is seat 0. Fine for marking a track, revisit if wrong.
- Where BeamMP writes io.open paths relative to: assumed server working dir
  (where ServerConfig.toml lives). If kkLap.log doesn't appear, that's why.

## Design decisions
- Server keeps the clock (MP.CreateTimer). Clients only report crossings.
- Every message carries vid: split-screen is one pid with two vids.
- Checkpoints are spheres (x,y,z,r), not BeamNG trigger objects, so no level editing
  and nothing from single-player Lua.
- One log tag [KKLAP] on both sides; server appends to kkLap.log so it can be read slowly.
- Linux only. Windows dropped (Darren, 26/10).

## Before any upload
    python3 Beam_mp/tools/check_lua.py && python3 Beam_mp/bench/bench.py && python3 Beam_mp/tools/build_client_zip.py

## Live test log
- 26/10 22:31 server time: main.lua and kkLap.zip uploaded via Pterodactyl to
  /home/container/Resources/..., restart, console shows "[KKLAP][I] kkLap loaded on Linux".
  Server is BeamMP-Server v3.9.3, Lua 5.3.5, 31 client mods, max 10 clients. (Darren, paste)

- 26/10 22:32: Darren joined (player 0). kkLap.log shows "cfg sent to 0" from onPlayerJoin
  and NOTHING from the client: no "extension loaded", no alive line. Client mod did not run,
  or the zip never reached the PC. (Darren, paste)
  Change made: modScript.lua moved to scripts/kkLap/modScript.lua to match BeamMP's own
  scripts/BeamMP/modScript.lua (cloned source). Guide diagram showed it one level up. Darren hit
  the same thing on his first attempt and had to move modScript into its own folder (chat 26/10). UNCHECKED
  whether that was the cause; the beamng.log grep on the PC decides.

- 26/10 ~23:10 (v0.2.0 session, before v0.3.0 was uploaded): Darren grepped beamng.log on the PC
  for "KKLAP kkLap". ONE hit: the MPCoreNetwork server-browser JSON. No "[KKLAP] modScript ran"
  line. **So the client zip never ran on the PC. Not a colon problem, not an onUpdate problem:
  the mod never loaded.** (Darren, paste)
  Clue in that same JSON, his server's entry: modstotal 31, modlist ends with "/;" = a mod
  with an EMPTY name. TResourceManager.cpp (server source, constructor): every entry in
  Resources/Client whose name ends in ".zip" is listed as "/" + (name with .zip cut off) + ";".
  An empty name means something in Resources/Client is called exactly ".zip" (a file or a
  folder: the constructor does not check is_directory). UNCHECKED which. That "modstotalsize"
  read "560002kkLap7901" in the paste; the server sends a plain integer there, so that is
  the paste garbled, not evidence.
  The beamng.log paste was huge and blew the chat context (Darren, 26/10). Rule from now on:
  never paste beamng.log; grep it on the PC and paste only matching lines.
  NEXT (one step): Darren pastes the Pterodactyl file listing of /home/container/Resources/Client
  (names and sizes), after uploading kkLap_v0.3.0.zip and deleting the old kkLap.zip. Then
  the console "Loaded N Mods" line and the modlist should show "/kkLap_v0.3.0;".
  Then rejoin and grep beamng.log on the PC for KKLAP again.

## First live test, in order
1. DONE. Plugin loads on the real server.
2. DONE. Console line seen.
2b. OPEN. Upload kkLap_v0.3.0.zip and the v0.3.0 main.lua, DELETE the old kkLap.zip from the
    server, restart, rejoin. Check beamng.log on the PC for
    "KKLAP". A "modScript ran" line means the zip arrives and runs.
3. Join in game. kkLap.log should get: [C<pid>] extension loaded, then cfg received.
4. Wait 10 s. kkLap.log should get "alive, own vehicles: <id>". If missing, onUpdate
   isn't wired: that's the first bug to fix.
5. /lap mark at a start line, drive 50 m, /lap mark again, drive round, cross both.
   Expect "timing started", a split, then a LAP line.

## Lap timer features to add once the skeleton runs (in order)
1. Per-driver best / last / delta to best, shown on screen as a small HUD app, not toasts.
2. Sector times with delta colours (green/red) like a real timing screen.
3. Leaderboard persisted to a file per map, survives restarts, /lap top shows it.
4. Direction check: a sphere crossed backwards must not count.
5. Checkpoint as a line (two points + width) as an option, for wide start/finish straights.
6. Invalidate lap on reset/respawn (server already sees onVehicleReset).

## Community
Darren has run a BeamMP server for a few years and wants this built for the community,
with more people involved. Keep the README readable by a server owner who doesn't code.

## Open questions for Darren
- Paste the exact error line from the old attempt's debug when you find it.
- Is the test server's spawn limit set to 2 or more for split-screen?
