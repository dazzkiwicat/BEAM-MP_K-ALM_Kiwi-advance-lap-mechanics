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
v0.6.0 honk countdown and sector laps LIVE. v0.7.0 (big centre flashes, running clock, short lap toast) bench-passed 48 checks, LIVE through v0.7.1 ("it all worked"). v0.13 (/dj pointer; commands without the lap word; music broadcast; resets counted, position resets invalidate, 2-lap minimum, points; /lap start, /lap finish, tarseal-only painted lines) bench-passed 75 checks, NOT yet live since v0.7.1. Next move: upload v0.13. Next move: upload v0.7.1; if no big text, add the two UI apps with Alt+U.

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

- 26/10: Darren: "they have to be see thru". UNCHECKED which version he saw and whether the
  sphere draw honours alpha at all (BeamMP uses 127 for its blobs). -> v0.5.2: one number
  cfg.markerAlpha (default 40 of 255, was 70-120) drives every marker colour, settable live
  with /lap set markerAlpha <n>, no re-upload to tune. If 10 still looks solid, the draw call
  ignores alpha and the next move is a different shape (ring of small spheres), not a number.
  NEXT (one step): upload v0.5.2 (main.lua + zip), restart, look; then /lap set markerAlpha 10
  and look again. Two looks settle whether alpha works.

- 26/10: Darren: countdown when sitting in the start bubble so people race together; then
  "even better: when 1 or more people honk inside the bubble it starts the timer for them,
  that is very aligned with how we start races intuitively" (chat). -> v0.6.0, bench 43 checks:
  honk inside the start bubble (before your first sector) -> server counts everyone down
  in chat and on screen (MP.CreateEventTimer, 1 s ticks, cloned source TLuaEngine.cpp:982)
  -> at GO every own car still in the bubble sends kkLap_cross{go=true} and the server sets
  its lapStart to the ONE GO instant, so laps compare. Jump start (on the line at countdown
  start, left the bubble before GO) announced to all. /lap race [sec], /lap autostart on|off,
  /lap set countdown. Sit-still trigger dropped: honk is intentional, parking is not.
  Horn comes from a vehicle-side file kkLapVE.lua loaded per car with
  extensions.loadModulesInDirectory from the GE onVehicleSpawned hook (BeamMP does exactly
  this, MPVehicleGE.lua:1683) and read as electrics.values.horn (UNCHECKED name on the read
  side; BeamMP only shows the setter electrics.horn(bool), MPElectricsVE.lua:422).
  Bench caught: GO start was rejected as "wrong cp" because a car rolling into the bubble
  had already crossed cp1; GO block now sits above that check. Vehicle file benched in its
  own LuaJIT VM: one call per press, number or boolean horn.
  v0.5.2 never uploaded; go straight to v0.6.0.
  NEXT (one step): upload main.lua v0.6.0 + kkLap_v0.6.0.zip, delete the old zip, restart,
  park in the start bubble, honk. Expect chat "race start in 5 (Darren)", 4..1, GO!, then
  the lap toast. If the honk does nothing: kkLap.log tells which side; look for "honked on
  the line" ([C0]) and "countdown started by".

- 26/10: **LIVE: lap toast with sectors seen** (screenshot): "guest7144128 lap 1 3:16.875
  [16.0 | 10.2 | 27.3 | ... 17 sectors ...] best 3:16.875". Darren: "ha that worked".
  So v0.5.0+ sector timing works on the real server and his track has 17 checkpoints.
  UNCHECKED from this: whether the honk countdown fired (not in the screenshot) and the
  km/h (toast shows no speed; speed only goes in the chat line, by design).
  Seen in the picture: 17 sectors in one toast is a wall. Fix for next version: toast shows
  the lap time and the last sector only; the full list stays in chat and laps.log.

- 26/10: **HONK COUNTDOWN WORKS LIVE** (Darren: "yes honk worked!"). Step 8 done. So the
  vehicle-side file loads per car, electrics.values.horn reads, queueGameEngineLua reaches
  our GE extension, MP.CreateEventTimer ticks. All now checked on the real server.
  Darren: writing too small to read; make the checkpoint times larger and flash.
  -> v0.7.0, bench 48 checks: ScenarioFlashMessage (big, centred, the game's own 3-2-1-GO
  display, with the UI_Countdown1/2/3/Go sounds) for countdown, GO, every sector, own lap;
  ScenarioRealtimeDisplay for a running lap clock (client estimate, labelled so). Both from
  docs.beammp.com/beamng/lua-snippets (web, 26/10). Each needs a stock UI app on the
  player's layout: "Race Countdown" and "Race Realtime Display" (Alt+U). UNCHECKED whether
  the default freeroam layout has them; toasts stay as the fallback either way.
  Lap toast now shows the last sector only (17-sector wall fixed). Server stamps yourPid on
  the config it sends each player so the client knows which laps are its own.
  NEXT (one step): upload v0.7.0, restart, drive through a checkpoint. If the big centre
  text does not appear: Alt+U, add "Race Countdown" and "Race Realtime Display", try again.

- 26/10: Darren: start line too faint once passed; wants black-and-white chequered with
  green. -> v0.7.1: start/finish never takes the grey "done" look; a band of 32 small
  spheres, black and white alternating, rings the start sphere at 0.4 m above its centre.
  Same two draw calls, nothing new to trust. UNCHECKED how it reads at speed; screenshot.
  Also tells us: alpha IS honoured (grey at half alpha was "too faint"), so markerAlpha works.
  v0.7.0 never uploaded; go straight to v0.7.1.

- 27/10: **v0.7.1 LIVE, "it all worked"** (big centre text, countdown beeps, running clock,
  chequered ring). Steps 7 and 9 done. But the ring of 32 balls blocks the view of the road.
  Darren: "flag that idea" (parked, not deleted: a ring of balls as a marker style).
  -> v0.7.2: ring removed, start sphere stays green and never fades. NOT uploaded yet;
  it will ship with the painted line below so he uploads once.
  Darren's design: the start/finish trigger gets its own behaviour: a real painted /
  textured race line on the road at the marked coordinates, across the road, and the mark
  only accepted when the car is on tarseal. Needs three BeamNG-side facts I don't have
  checked: ground material under the wheels (vehicle Lua), a flat draw shape or a runtime
  DecalRoad/decal, and the line's heading (veh:getDirectionVector, used 26 times in BeamMP's
  own client, so that one is safe). Research brief handed to an Opus agent 27/10:
  scratchpad/research/BRIEF_startline.md -> RESULT_startline.md. Build waits on the result.
  Research came back 27/10 (Opus agent, 57 tool calls). Sources: stock game Lua dump on GitHub
  (wlkmanist/BeamNG_lua, newer than 0.36), rally-academy mod notes with per-row in-game checks
  on 0.39.4/0.40.0, BeamMP client source. Verified: wheels.wheels[i].contactMaterialID1/2 with
  4=RUBBER swapped out and particles.getMaterialByID for the name (HIGH); FFI
  BNG_DBG_DRAW_SquarePrism(x1,y1,z1,x2,y2,z2,baseH,baseW,tipH,tipW,col,useZ), 2 cm high lies
  flat (HIGH); runtime DecalRoad via createObject/setField/registerObject/insertNodeNoRegen/
  regenerate, delete with :delete() (MEDIUM-HIGH); material "checkered_line" seen in RLS West
  Coast level files, UNCHECKED on other maps. GE-side terrain lookup does not see roads, so the
  tarseal check must come from the car. TSStatic made live drew nothing on 0.40 (not used).
  -> v0.8.0, bench 54 checks: /lap mark asks the car's Lua (kkLapVE.reportSurface) and the
  car answers kkLap.onSurface(id, sealed, "FL=ASPHALT,..."); first mark refused unless every
  touching wheel is sealed; heading stored as hx,hy on cp1; line across the road, lineWidth m;
  lineStyle paint (DecalRoad, checkered_line) with automatic fallback to draw if createObject
  throws, and /lap line draw by hand if the material shows orange. 1 s timeout if the car
  never answers ("UNKNOWN", refused). Ring of balls stays parked.
  UNCHECKED live: checkered_line on his map; DecalRoad z on mesh roads (overObjects=true);
  getSurfaceHeightBelow miss value (guarded); hx/hy sign (line could come out along the road
  instead of across: if so, swap rx,ry in lineGeometry).
  NEXT (one step): upload v0.8.0 (main.lua + zip, delete old zip), restart, /lap clear, park
  on the tarseal start, /lap mark. Expect the chat line to accept and a chequered line across
  the road. Then try /lap mark on grass: expect the refusal with the wheel list.

- 27/10: Darren: "/lap start and /lap finish, both locked together like in this case. If I
  edit the start line now I have to redo all the checkpoints, right?" Yes, he was right:
  cp1 was only ever the first /lap mark, so moving it meant /lap clear. Also: "keep the honk
  mechanic". -> v0.09, bench 61 checks: /lap start replaces checkpoint 1 in place (radius
  kept unless given), nothing else touched; /lap finish stores cfg.finish = a separate line
  (tarseal + heading, own chequered line, label FINISH, start label becomes START); course
  becomes point-to-point: start only starts, finish ends the run, back to start to go
  again; /lap finish same = loop again. Client targets list = checkpoints + finish. Honk
  untouched: it keys off checkpoint 1, which is still the start in both modes.
  Bench caught a real one: the server ms clock used floor(seconds*1000), and 59.3*1000 is
  59299.999 in floating point, so a lap could read 1 ms short. Now rounds. Would have
  shown live as laps 1 ms off between players at the same GO.
  v0.8.0 never uploaded; go straight to v0.09.
  NEXT (one step): upload v0.09 (main.lua + zip, delete the old zip), restart, /lap clear,
  park on tarseal, /lap start, then /lap mark round the course, drive a lap. Then test
  /lap finish somewhere and a run start->finish; then /lap finish same.

- 27/10: Darren: count physics resets (allowed, shown in the output) but not position
  slews (cheating); minimum 2 laps before any lap counts for the record; a simple points
  system, closer to the top time = more points, only once a few people have times.
  -> v0.10, bench 72 checks. Reset detection: the car's Lua onReset (hook proven live via
  BeamMP's own VE files) calls kkLap.onVehReset(id); GE compares with the last position it
  advanced by normal driving: moved > 2 m = position reset -> kkLap_invalid, server tells
  everyone at once and refuses the lap at the line (laps.log gets a NOT COUNTED line);
  else physics reset, counted, sent with the next crossing, summed per lap, in the LAP
  line and board. Backstop: a jump > 100*dt+150 m in one frame with no reset event within
  1 s = teleport, invalid. (First cut was 15 m and the bench's 50 m hops tripped it: the
  bench teleports the fake car; real cars don't. Coarse is right here, the reset path is
  the real judge.) Board: entry only when r.laps >= minLaps (2); best over all laps.
  Points: floor(100*top/yours), shown when >= pointsMinDrivers (3) on the board.
  UNCHECKED live: that recover/respawn fire the vehicle onReset AFTER the move (if before,
  moved reads 0 and a recover counts as a physics reset; the backstop would still catch a
  big respawn). Test by recovering mid-lap and reading the chat.
  v0.09 never uploaded; go straight to v0.10.
  NEXT (one step): upload v0.10 (main.lua + zip, delete the old zip), restart, drive a lap
  with one Insert reset (expect "physics reset 1 (counted)"), then a lap with one R recover
  (expect "lap will not count: position reset").

- 27/10: Darren: a soundtrack the server can play, pulled from YouTube? -> v0.11, bench 75:
  /lap music <name> [vol] broadcasts kkLap_music; each client plays art/sound/kkLap/<name>.ogg
  from the zip. Engine.Audio.playOnce('AudioGui', 'art/sound/x.ogg', {volume=}) is used by
  BeamJoy (a BeamMP client mod), RLS career overhaul and a BeamMP RP plugin (GitHub code
  search 27/10), so .ogg from a mod zip is proven in multiplayer. Stoppable version:
  Engine.Audio.createSource(ch, path) -> id, source:play(-1), :setVolume, deleteSource(id)
  (RLS musicPlayer.lua). UNCHECKED: scenetree.findObjectById(id) as the way from id to
  the source object (RLS line not seen); pcall'd with playOnce fallback, so worst case is
  no stop and no volume change. No track in the repo yet: Darren supplies an .ogg (ffmpeg
  is on this box, I can convert an mp3 he commits). Copyright: the zip goes to every
  joiner = distribution; YouTube Audio Library / CC tracks are the safe pick. His call.
  NEXT (one step): Darren commits a small .ogg (or mp3 for me to convert) into
  Resources/Client/kkLap/art/sound/kkLap/, I rebuild the zip; or upload v0.11 now and
  test the rest.

- 27/10: Darren: drop the need for "/lap" in chat. -> v0.12, bench 78: "/mark 10" = "/lap mark 10"
  for every command word we own (SHORT table in main.lua); any other "/word" is passed
  through untouched for other plugins; "/lap ..." still works. Server-only change, client
  version bumped to keep the numbers in step.
  NEXT (one step): upload v0.12 (main.lua + zip, delete old zip), restart, type /help.

- 27/10: Darren: a YouTube DJ set on repeat; can the server reach YouTube? Answer given:
  the server has no path to anyone's speakers (BeamMP carries the zip at join + text
  events, nothing else), so the DJ booth is Discord voice with their existing music bot.
  -> v0.13: /dj [text] broadcasts a remembered pointer to the voice channel. Instructions
  for players written in the reply (join the voice channel on the same PC, bot volume via
  right-click > User Volume). UI-app YouTube embed noted as an UNCHECKED experiment, not built.
  NEXT (one step): upload v0.13 (main.lua + zip, delete old zip), restart, /dj <your text>.

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
7. OPEN. v0.6.0: chat line per lap with sectors and km/h; laps.log on the server.
8. DONE. v0.6.0 honk -> countdown -> GO works live. Markers see-through: still UNCHECKED.
9. OPEN. v0.7.0: big centre text for sectors and laps; running clock. UI apps on layout?

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
