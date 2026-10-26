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
Live status 26/10 ~23:30: **v0.3.0 timed a real lap on the real server** (Darren: "seems to
work"). v0.4.0 markers LIVE and seen. v0.5.0 (sectors, speed, chat announce, laps.log, /lap times)
plus v0.5.1 (crossed markers flash then grey) bench-passed 32 checks, NOT yet live. Next move: upload v0.5.1.

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

- 26/10 23:18 server time: **v0.3.0 WORKS BOTH WAYS.** kkLap.log: 23:18:10 v0.3.0 loaded on Linux;
  23:18:45 "cfg sent to 0" then "[C0] cfg received: 0 checkpoints, debug=true". (Darren, screenshot)
  That one [C0] line proves: zip reached the PC and ran, modScript ran, extension loaded,
  server->client (kkLap_cfg) and client->server (kkLap_dbg) both deliver. Steps 1-3 DONE.
  Two things the log did NOT show, both explained, neither blocking:
  a) no "[C0] extension v0.3.0 loaded, asking server for config" line, and only ONE "cfg sent
     to 0" (onPlayerJoin's). The client's first sends happen before the session is up, so
     kkLap_getcfg and that dbg line are dropped. onPlayerJoin covers it. Fine as is.
  b) no "alive, own vehicles" line. Design flaw, mine: in kkLap.lua onUpdate the 5 s alive
     line sits BELOW "if n == 0 then return end", so with 0 checkpoints it never fires. Step 4
     cannot pass before /lap mark. Fix in v0.3.1: move the alive block above the early return.
     Not re-uploading for that alone; it rides with whatever the first lap test finds.
  NEXT (one step): in game, at the start line, type /lap mark. Expect chat "[LAP] checkpoint 1
  marked at x y z r=8.0" and config.json appearing beside main.lua.

- 26/10 ~23:30 server time: **FIRST LIVE LAP TIMED.** /lap mark showed the marker lines in
  chat, Darren drove the loop, "seems to work" (Darren, chat 26/10). Steps 5 done. Screenshot
  of kkLap.log for that lap not taken; the alive and cross lines are UNCHECKED on the real
  server but the lap toast could not appear without them.
  Darren's ask: see the start/finish and checkpoints in the world, like single-player.
  -> v0.4.0 written and bench-passed (19 checks): checkpoint spheres drawn in the world
  from onPreRender with the two FFI draw calls BeamMP's own MPVehicleGE.lua uses for name
  tags (BNG_DBG_DRAW_Sphere, BNG_DBG_DRAW_TextAdvanced, color()), fetched inside a pcall so
  a missing call means no markers, not a dead mod. /lap markers on|off. Alive line moved
  above the empty-checkpoint exit (flaw b above, fixed). Old config.json gets markers=true.
  UNCHECKED: that those FFI calls draw on Darren's 0.40 build. Evidence for: BeamMP v4.23.1
  (the 0.40 build) still calls them (cloned source). The first screenshot decides.
  The single-player race-gate look (flags, arch) is NOT this: searched the web 26/10, no
  public name for that GE Lua API; BeamNG's game Lua ships inside the install folder, not
  online. Optional later: Darren pastes a dir listing of <BeamNG install>\lua\ge\extensions
  (or says it isn't there) and I can look for the real race marker file by name.
  NEXT (one step): upload main.lua v0.4.0 and kkLap_v0.4.0.zip, DELETE kkLap_v0.3.0.zip,
  restart, rejoin, screenshot. Expect blue/green/orange translucent balls on the track with
  labels; kkLap.log "[C0] extension v0.4.0 loaded, markers available".

- 26/10 later: **v0.4.0 LIVE, markers visible** (Darren: "works well", chat). Step 6 done.
  Asks: sector times visible to everyone (chat announce preferred), and speed through a
  checkpoint, "enter one side of the bubble, out the other".
  -> v0.5.0 written, bench 27 checks: sector = since previous checkpoint, split = since the
  line, both toasted to the driver; every finished lap announced to all in chat with the
  sector list; laps.log (append only) and times.json (best per name) beside main.lua;
  /lap times, /lap announce on|off. Speed: read off the car with veh:getVelocityXYZ() at the
  frame it enters the sphere, not timed across the bubble: the chord through a sphere is only
  the diameter if you hit it dead centre, so the bubble method reads low when you clip the
  edge, and the game already knows the velocity. getVelocityXYZ is what BeamMP's own
  MPVehicleGE.lua:2605 calls on a vehicle (cloned source). UNCHECKED on the live server.
  NEXT (one step): upload main.lua v0.5.0 + kkLap_v0.5.0.zip, delete the v0.4.0 zip, restart,
  drive a lap, screenshot the chat line and laps.log.

- 26/10: Darren: make it obvious when you have been through a checkpoint. -> v0.5.1 (client
  only, server just bumps its version text): crossed checkpoint flashes white 1.5 s, label
  "OK", then grey ghost labelled "done" until the next lap starts; next stays blue. Colour
  logic is a pure function M.cpStyle so the bench tests it without the game (32 checks).
  v0.5.0 was never uploaded; go straight to v0.5.1.
  NEXT (one step): upload main.lua v0.5.1 + kkLap_v0.5.1.zip, delete the old zip, restart,
  drive a lap, screenshot: chat lap line, a white flash, a grey done marker.

## First live test, in order
1. DONE. Plugin loads on the real server.
2. DONE. Console line seen.
2b. DONE 23:18. v0.3.0 uploaded, old zip gone, client runs on the PC.
3. DONE 23:18. kkLap.log got "[C0] cfg received: 0 checkpoints".
4. MOVED after step 5: the alive line only fires once a checkpoint exists (flaw, see log).
   After the first /lap mark, wait 10 s: "alive, own vehicles: <id>" must appear. If it
   doesn't, onUpdate isn't wired: that's the first bug to fix.
5. DONE ~23:30. /lap mark at a start line, drive 50 m, /lap mark again, drive round, cross both.
   "timing started", a split, then a LAP line. Darren: "seems to work".
6. DONE. v0.4.0 markers visible in the world (Darren: "works well").
7. OPEN. v0.5.0: chat line per lap with sectors and km/h; laps.log on the server.

## Lap timer features to add once the skeleton runs (in order)
1. Per-driver best / last / delta to best, shown on screen as a small HUD app, not toasts.
2. Sector times with delta colours (green/red) like a real timing screen.
3. Leaderboard persisted to a file per map, survives restarts, /lap top shows it.
4. Direction check: a sphere crossed backwards must not count.
5. Checkpoint as a line (two points + width) as an option, for wide start/finish straights.
6. Invalidate lap on reset/respawn (server already sees onVehicleReset).

## Ship log (what left the building, by whom)
- 26/10 (server time ~23:50): **Darren published the first GitHub release himself.**
  Tag "Beam-MP", pre-release, commit b18c4f0 = kkLap v0.4.0 (markers build). Title says
  "latest 0.43 update"; our record says BeamNG 0.40 (19/10): UNCHECKED which he runs.
  Assets: GitHub's own source zip/tar only; the built kkLap_v0.4.0.zip is inside it at
  Beam_mp/Resources/Client/. Release notes: "works mint for now, not tested much yet".
  Trap: the lap that was tested live was v0.3.0; v0.4.0's markers were NOT seen live when
  the release went out. Markers fail soft by design, timing is unchanged, so the release
  is usable; the markers are still UNCHECKED.
  Next release: tag it with the version (kkLap-v0.4.1) so two releases never share a name.

## Community
Darren has run a BeamMP server for a few years and wants this built for the community,
with more people involved. Keep the README readable by a server owner who doesn't code.

## Open questions for Darren
- Paste the exact error line from the old attempt's debug when you find it.
- Is the test server's spawn limit set to 2 or more for split-screen?
