# kkLap STATE  (live file: read this first, update it last)

Updated: 2026-10-30 by Claude (v0.22). Earlier: 2026-10-26. Current version: 0.3.0 (server main.lua and client zip share it). Branch: claude/blissful-mccarthy-eg6jed

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
v0.6.0 honk countdown and sector laps LIVE. v0.7.0 (big centre flashes, running clock, short lap toast) bench-passed 48 checks, LIVE through v0.7.1 ("it all worked"). v0.15 LIVE. v0.19 LIVE. v0.21 pushed (music autoplay on car load; deltas, id-keyed board, wrong way, Discord poster script), ran 5 days live, three bugs found (30/10). v0.22 fixes them, bench 128, NOT yet live. v0.23 (race timer on the HUD, no sector/lap toasts) bench 151, NOT yet live. Still to prove with two cars: everyone-honks start, race numbers. Admin list not yet locked. ( commands without the lap word; music broadcast; resets counted, position resets invalidate, 2-lap minimum, points; /lap start, /lap finish, tarseal-only painted lines) bench-passed 75 checks, NOT yet live since v0.7.1. Next move: Darren commits a track; upload v0.14. Next move: upload v0.7.1; if no big text, add the two UI apps with Alt+U.

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

- 26/10: **LIVE: lap toast with sectors seen** (screenshot): "guest1234567 lap 1 3:16.875
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
  own client, so that one is safe). Research brief handed to a research agent 27/10:
  scratchpad/research/BRIEF_startline.md -> RESULT_startline.md. Build waits on the result.
  Research came back 27/10 (research agent, 57 tool calls). Sources: stock game Lua dump on GitHub
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

- 27/10: Darren first: "transmitting as a live file over the game", then "back to choice 2".
  Parked finding for the live-stream day: BeamMP's own MPCoreNetwork.lua:303 runs
  be:executeJS(...) in the game's Chromium UI, so GE Lua could create an <audio src=URL>
  element there and play an Icecast stream with no UI app on the layout. UNCHECKED: codecs
  in that Chromium build, and he'd need an Icecast source somewhere (his PC or a VPS; the
  BeamMP container cannot run it). Not built.
  -> v0.14: /music loops by default (isPlaying poll once a second, restart when it stops;
  UNCHECKED that source:isPlaying exists, pcall'd), "once" to play through, or a number of
  seconds as a timed re-fire for the playOnce fallback. Waiting on an audio file from
  Darren: commit mp3/ogg into Resources/Client/kkLap/art/sound/kkLap/, I convert with
  ffmpeg (here: /usr/bin/ffmpeg) to 64-96 kbps ogg and rebuild the zip. Size warning given.
  27/10: Darren: server mods already total 4 GB, size is no issue. Design settled: audio
  never enters git (would bloat the repo for good) and never enters the code zip (which
  changes every version). It goes in its own client zip, kkLap_music.zip, containing only
  art/sound/kkLap/<name>.ogg; BeamMP mounts all zips, the game finds the file. .gitignore
  added for audio in that folder. Darren gives me a download link to the set; I convert
  with ffmpeg here and hand the music zip back with the file-send tool.
  27/10: Darren has the set: 1.5 h mp3, ~112 MB. No conversion needed: the same play call
  takes mp3 (RLS musicPlayer globs *.mp3). v0.15: /music accepts a name with extension.
  He builds kkLap_music.zip on his PC (folder art\sound\kkLap\set.mp3, send to compressed
  folder, rename) and uploads it to Resources/Client beside the kkLap zip.
  NEXT (one step): upload kkLap_music.zip + main.lua v0.15 + kkLap_v0.15.zip, delete the old
  kkLap zip, restart, /music set.mp3 0.4. Expect "music: set.mp3 (Darren, volume 40%, on
  repeat)" in chat and sound on every PC; kkLap.log "[C0] music playing art/sound/kkLap/set.mp3".

- 27/10: **v0.15 LIVE, music plays on every PC** (Darren: "works"). That upload also carried
  everything since v0.7.1: /start, /finish, tarseal-only painted lines, resets, 2-lap board,
  points, bare commands, /dj. Each of those is live but UNCHECKED individually until he
  reports on it (painted line look, recover-vs-reset judgement, points display).
  Too quiet even at 100% (YouTube rips are mastered low). -> v0.16: volume above 1 passes
  through as a boost, capped at 10 (RLS passes volume=2 to playOnce; UNCHECKED whether the
  stoppable source clamps at 1). Real fix is in the file: Audacity Amplify/Normalize, or
  ffmpeg -af loudnorm. Also worth checking: the game's Options > Audio sliders, AudioGui
  is the interface channel.
  27/10: solved, nothing to do with the file: Darren's "Other" slider in Options > Audio
  was turned down. So AudioGui = the "Other" slider (checked live). Written into README.
  The v0.16 boost stays, harmless.
- 27/10: **painted start line WORKS live** (so the checkered_line material is on his map and
  runtime DecalRoad creation works on 0.40), and **R recover mid-lap is caught** as a position
  reset (so the car's onReset fires after the move, and the 2 m judgement holds). Darren:
  "well done". Both moved from UNCHECKED to checked.
  Still UNCHECKED: points display (needs 3 drivers), /finish point-to-point on the real
  server, the stoppable-music path vs playOnce fallback (which one ran: kkLap.log says
  "music playing" either way, look for "createSource failed").
- 27/10: BUG live: after a position reset disqualified the lap, going back into the start
  bubble and honking did nothing. Cause: honk was gated on "before the first sector"
  (nextCp <= 2) and the client only watched the NEXT target, so re-entering the start
  mid-lap was invisible. Darren's rule: "it's always got to work for everyone if they start
  back there regardless, and reset their last lap if only partially completed".
  -> v0.17, bench 86: the client edge-detects the start bubble separately from the current
  target; entering it with nextCp ~= 1 sends kkLap_cross{restart=true}: server drops the
  partial lap, timing starts fresh, "timing started"; honk arms from any state. GO already
  restarted everyone in the bubble.
  NEXT (one step): upload v0.17 (main.lua + zip, delete old zip), restart, reproduce: mid-lap
  R recover, drive back to the start, honk. Expect the countdown.

- 27/10: Darren's start rule: one honk from EVERYONE in the bubble is required; alone, your
  honk starts it for you; a later arrival starts their own; same-GO times are referenced
  together. -> v0.18, bench 89: clients report entering/leaving the start bubble
  (kkLap_bubble on the edge); server keeps the bubble roster; a honk marks that car ready
  and the countdown starts when ready == waiting (cars not yet started); at GO the group is
  marked started so late arrivals form the next group; race.no increments per GO and
  stamps every lap from that GO ("race 3" at the end of the lap line). /lap race (manual)
  still starts regardless. Roster cleared on every config push; clients re-report.
  NEXT (one step): upload v0.18 (main.lua + zip, delete old zip), restart, two cars in the
  bubble: first honk should print "ready (1 of 2)", second honk starts the countdown.

- 27/10: Darren: needs its own UI window for rules + player commands; config is admin only.
  Also: music keeps playing after leaving the level. -> v0.19, bench 97:
  * rules window: ui_imgui (the binding BeamMP's MPDebug.lua draws from onUpdate: im.Begin,
    im.Text, im.Button, im.End, cloned source), whole draw in a pcall so a binding mismatch
    closes the window instead of killing the frame. Opens on join (cfg.rulesOnJoin) and
    toggles with /rules; Close button. Shows cfg.rules lines (defaults written from the
    rules we built; /rules add, /rules clear), the player commands, and the admin commands
    only for admins. UNCHECKED: im.SetNextWindowSize/Cond_FirstUseEver and im.TextWrapped
    names in this build (pcall covers it; if the window never shows, kkLap.log says
    "rules window failed to draw").
  * admin gate: cfg.admins (names). Empty = everyone (bootstrap); first /admin add locks it.
    ADMIN_ONLY set covers every command that changes anything. /help is split.
  * music: onClientEndMission now also stops the music (it only stopped the line before).
    If the playOnce fallback was what ran, there is no stop; kkLap.log "createSource failed"
    would say so.
  NEXT (one step): upload v0.19 (main.lua + zip, delete old zip), restart, join: the rules
  window should pop up. Then "/admin add <your in-game name>" to lock config to you.

- 27/10: **v0.19 LIVE, rules window shows** (screenshot: title, RULES, COMMANDS, ADMIN,
  Close button all drawn, so SetNextWindowSize / TextWrapped / Cond_FirstUseEver exist in
  this build: UNCHECKED cleared). Darren: "looks good". With it live: v0.17 start-bubble
  restart and v0.18 everyone-honks rule, both still UNCHECKED on the real server until he
  runs them with two cars.
  NEXT (one step): /admin add <in-game name>. Then two-car honk test when someone is on.

- 27/10: Darren picked 1, 3, 4, 6 from my list. -> v0.20, bench 103:
  1 sector deltas: server keeps bestSectors per run, seeded from the board, deltaMs on every
    split and on the lap (vs previous best); client prints "%+.1f".
  4 board keyed by "id:<beammp account id>" from MP.GetPlayerIdentifiers (key "beammp",
    server source TNetwork.cpp:481 / TLuaEngine.cpp:1418), name kept for display; falls
    back to the name when there is no id. Old name-keyed entries still sort.
  6 wrong way: velocity dot the stored start heading < 0 while moving = WRONG WAY flash,
    crossing swallowed (both the normal cp1 crossing and the mid-lap restart edge).
  3 tools/discord_poster.py: reads laps.log via the Pterodactyl client API, posts unseen
    lines to a Discord webhook, cursor file; dry-run proven on a sample log (prints the
    batch, then "nothing new"). NOT yet run for real: needs PTERO_URL, PTERO_API_KEY,
    PTERO_SERVER_ID, DISCORD_WEBHOOK as environment secrets, and the panel host + discord.com
    allowed by the environment's network policy. Then a scheduled task (hourly is the floor)
    or Darren runs it on his PC with those four variables set.
  NEXT (one step): upload v0.20 (main.lua + zip, delete old zip), restart, drive two laps:
  second lap's sector flashes should carry +/- numbers. Separately: set the four secrets.

- 27/10: Darren: music should start when the car loads, not on a command, admin sets it.
  -> v0.21, bench 107: cfg.musicAuto (+ musicVolume) set by the admin's /music <name>,
  cleared by /music stop, persisted; client starts it from onVehicleSpawned (own car) or on
  config arrival if a car exists; only restarts when the track changes (music.active flag,
  so the config push on every admin command, and respawns, leave it alone).
  NEXT (one step): upload v0.21 (main.lua + zip, delete old zip), restart, /music set.mp3 0.4
  once; then leave and rejoin: it should play by itself when your car appears.

- 30/10: three bugs from 5 days of v0.21 on the server -> v0.22, bench 128 (was 107).
  1 START BUBBLE JAMMED: two cars in the bubble, the second never honked or was respawned,
    the race could never start; the owner had to leave and rejoin. Root cause (main.lua): a
    bubble entry only goes on a client {inside=false} or onPlayerDisconnect. A DELETED car
    (Ctrl+R = new vehicle id, or removed) has no onUpdate pass left to send inside=false, so a
    stale {ready=false} entry sat there and ready >= waiting was never true.
    Fix: client sweeps its state once a second and drops any vid be:getObjectByID no longer
    finds (BeamNG) or MPVehicleGE.getOwnMap no longer lists (BeamMP), sending inside=false if
    it was in the bubble; onVehicleDestroyed(vid) does the same at once. Server re-counts on
    every leave (and on a disconnect): everyone left has honked = countdown "<name>, last car
    left". And a SECOND honk from a car already ready starts the countdown without the rest
    ("<name> honked twice: starting without the rest"); everyone-honks stays the default.
    UNCHECKED live: whether onVehicleDestroyed fires on Ctrl+R (if Ctrl+R keeps the same vid
    the sweep never needs to fire, and the honk-twice rule is the way out either way).
  2 MUSIC ONLY AFTER A RESET: autoplay started on Ctrl+R, not on the first car. Root cause
    (BeamMP client MPVehicleGE.lua): a local car joins the own map in onServerVehicleSpawned,
    after the server round trip, AFTER the GE onVehicleSpawned hook; musicAutoCheck returned
    while the own map was empty, so the first spawn was missed.
    Fix: no own-map gate, it plays the moment the config arrives (on join, before any car).
    music.active is now true only if a play call worked; a refused autoplay is retried every
    3 s, at most 10 times, counter reset by each config. Each try is a dbg line.
    UNCHECKED live: whether the game plays audio before the first car exists (the retries
    cover a refusal for 30 s; beamng.log "music autoplay retry" lines tell).
  3 SPLIT SCREEN SHOWS NOTHING: the countdown and messages need the Race Countdown and chat
    apps, which have no room in two narrow windows. Fix: kkLap's own imgui HUD, top centre,
    no title/background/input, needs no UI app. Every toast()/flash() also feeds it; it draws
    the newest big line (digits, GO, WRONG WAY, sector flash) and up to 4 small ones. The
    server's race-start lines ("X ready (n of m)", "race start in N", "jumped the start",
    "honked twice") also go out as kkLap_hud via sayHud(); other chat lines stay chat only.
    cfg.hud (default true), admin /hud on|off. Drawn in a pcall; a failure turns it off and
    logs "HUD failed to draw".
    UNCHECKED live: im.SetWindowFontScale in BeamNG's binding (falls back to im.PushFont3
    "cairo_semibold_large", font name UNCHECKED too, then plain text; beamng.log says "HUD big
    text uses scale|font3|plain"); im.WindowFlags_AlwaysAutoResize (OR-ed in only if present);
    im.Begin(name, nil, flags) with a nil open-pointer.
  Bench found one more while writing it: a countdown digit expires a frame before the next one
  lands, so an older big line (a lap flash) showed through between digits. A new big line now
  replaces the old ones.
  Trap: config.json on the server keeps its OLD rules list (missing keys are filled, existing
  ones are not), so the new default rule line "Honk twice to start without the rest" only
  shows after /rules add, or after removing "rules" from config.json.
  NEXT (one step): upload v0.22 (main.lua + kkLap_v0.22.zip, delete kkLap_v0.21.zip),
  restart, two cars in the bubble, one honks twice: countdown must start.
- 30/10: v0.23, the race timer on the HUD like single-player time trials. Bench 151 (was 128).
  Darren (live, v0.21): "the timer, or at least how far I am ahead of last lap, is small and hard
  to read and distracting". The clock was the Race Realtime Display app and every sector a small
  top-left toast; the toast was the distraction.
  Now (client kkLap.lua): a second imgui window "##kkLapTimer", same flags and pivot as the HUD,
  top centre, 40 px under the HUD window (imgui.GetWindowHeight, used by BeamMP chat.lua and stock
  gui.lua; guessed from line counts if absent) or at y=90 when the HUD is empty. BIG running clock
  (or "LAP  m:ss.mmm" held 4 s after a lap), MEDIUM gap 3 s after each split AND after the line
  (red ImVec4(1,0.35,0.35,1) "+x.xx" slower, green (0.35,1,0.35,1) "-x.xx" faster or equal, the
  server's deltaMs sign; nothing when deltaMs is absent), SMALL "best m:ss.mmm" from kkLap_lap's
  bestMs (already in the payload, main.lua unchanged but for VERSION).
  Clocks are per car now ([vid], from kkLap_info's vid); the window follows be:getPlayerVehicle(0)
  (BeamNG GE) if that car is ours (MPVehicleGE.getOwnMap, BeamMP) and timing, else the lowest own
  vid that is timing. A point-to-point run's clock goes away after the LAP hold.
  Gone on the HUD path: the sector toast + flash (onSplit), the lap toast + flash (onLap), the
  realtime() clock. Chat lines are unchanged. The lap beep that rode on the flash is played
  straight from GE (Engine.Audio.playOnce 'event:UI_CountdownGo', BeamNG, in a pcall).
  Fallback: /hud off, or an imgui error in either window (hud.broken), puts the v0.22 path back
  (realtime() clock, sector/lap toasts and flashes). "lap timer path: ..." is logged on each change.
  UNCHECKED live: text sizes look right in a narrow (split-screen) window; red/green readable on a
  bright sky (no background, NoBackground flag); the lap beep from GE (UI event name played from
  GE Lua); the 40 px gap with GetWindowHeight in BeamNG's binding (falls back to a guess).
  Known, not fixed: a lap the server rejects (too short) or does not count (position reset) sends
  no kkLap_lap, so the client clock keeps running from the previous line until the next lap or
  "timing started". Same as before; it is just bigger now.
  NEXT: upload main.lua + kkLap_v0.23.zip (delete kkLap_v0.22.zip), drive a lap: big clock under
  the HUD, red/green gap at each checkpoint, best line after the first lap, no toasts.

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

## Lessons baked from the v0.3.0 to v0.22 thread (30/10, before a fresh session)

What five live days taught that the bench could not. All three v0.22 bugs were LIFECYCLE faults,
not logic faults: a car that vanished without saying goodbye, a hook that fires before the data
it needs exists, and a screen that had no room for the UI apps the messages relied on. The bench
models one tidy client that never dies and always has a car. Rules that came out of it:

1. Any roster keyed by a vehicle id needs a prune path for ids that vanish without a leave
   message. Ctrl+R gives a NEW game vehicle id; the old one never sends "inside=false".
   Same rule as "write once and don't delete" seen from the other side: a delete that never
   arrives is a silent failure.
2. A hook firing is not the same as the data being ready. BeamMP adds your own car to
   getOwnMap() only after the server echoes the spawn, which is AFTER BeamNG's onVehicleSpawned.
   Gate on the thing you need (a car object), not on a map filled by someone else later, or poll.
3. Never depend on an optional UI app for a message that decides a race. The "Race Countdown"
   and chat apps are layout choices on the player's PC; imgui is always there. Every flash goes
   to both, and the HUD is the one we trust.
4. A rule that needs everyone to act needs an exit for when someone won't. "Everyone honks" is
   right as the default; "honk twice to go without them" is the governor, not a loophole.

Working method that held up:
- Brief file in the scratchpad, Opus builds from it, bench + check_lua + zip run after, I review
  the diff stat and the bench totals, then push. Deciding thread stays small. (Darren, 30/10:
  "get opus to do these tasks, be token friendly".)
- Every API fact labelled: CONFIRMED (seen live or in the stock/BeamMP source in the scratchpad
  ref/ and research/dl/ folders) or UNCHECKED. Live confirmations from Darren are the only thing
  that clears an UNCHECKED; a clean bench run never does.
- Darren edits the root README on GitHub web while I work. Push rejected = fetch + merge --no-edit,
  never rebase, every time. Happened three times, never lost anything.
- Patch scripts assert their anchors and abort before writing. Four anchor misses, zero half-patched files.
- The window: /compact squeezes it; "collapse" is not a command. STATE.md is the resume point,
  and a fresh session starts by reading its top section and the last dated block.

Traps that bit once and are now coded against (keep, do not re-learn):
- Server event names never contain ':' (the wire splits at the first colon).
- nowMs() rounds; floor() lost 1 ms per lap.
- GO and restart branches sit ABOVE the wrong-checkpoint check in onCross; a car rolling into
  the bubble has already crossed cp1.
- Vehicle onReset fires AFTER a recover has moved the car, so "moved > 2 m" is a position reset,
  else a physics reset. Confirmed live 27/10.
- lupa: list(luatable) gives keys, use .values(); the client runtime is luajit21.LuaRuntime.
- Forward-declare locals used before their definition (buildLine, musicAutoCheck).
- Music plays through the game's "Other" slider; the file never enters git or the code zip.

Still UNCHECKED live after v0.22 ships (test in this order, cheapest first):
 1. Music starts on first join, before or at the first car.
 2. HUD shows the countdown in a narrow window with no UI apps on the layout.
 3. Two cars: second one Ctrl+R's away, countdown starts for the first.
 4. Two cars: second never honks, first honks twice, countdown starts.
 5. v0.20 deltas, wrong-way guard, id-keyed board; race numbers; point-to-point /finish; points
    display with three drivers; stoppable-music path vs playOnce fallback.

Parked ideas, with the reason: ring of balls at the start (blocked the view); YouTube stream
(server has no speakers, no HTTP in the server API); live stream via be:executeJS (possible,
nobody asked yet); CONTRIBUTING/issues/releases (Darren, 30/10: "forget about the contributing").
Open for Darren: rewrite history to remove author emails or leave it; set the four poster secrets.

Log evidence (30/10, from the server's own kkLap.log, 26-28/10, 20,383 lines; files stay in the
upload folder, never in the repo): bug 1 = player 1's vehicle id changed 192530 -> 198855 -> 201227
on 28/10 21:32-21:41 (three Ctrl+R), then 14 honks from both cars and 0 countdowns; the one
countdown of the evening was 21:28, before the respawns. Bug 2 = cfg received 21:08/21:09, music
started 21:22/21:37. BeamMP Server.log: no kkLap errors in 5 days; "Invalid code got in Identify"
and the heartbeat timeout are BeamMP's own, pre-date the mod (Darren).
SECURITY 30/10: ServerConfig.toml with the live AuthKey was uploaded into this session's upload
folder. Not in git, not quoted anywhere. Told Darren to have the owner regenerate it at keymaster.
Web leaderboard: Beam_mp/leaderboard/ (index.html reads times.json; README with the Caddy
read-only mount for the owner). Not deployed, UNCHECKED on a real host.

## Community
Darren has run a BeamMP server for a few years and wants this built for the community,
with more people involved. Keep the README readable by a server owner who doesn't code.

27/10: MIT licence added at the repo root (LICENSE), Darren picked it from the onboarding
list ("2 of licence"). Copyright line names Darren (Dazzcat) and the kkLap contributors.
Still on the list, not started: CONTRIBUTING.md, five starter issues, a GitHub Release
per version with both zips, a draft forum/Discord post, root README front door.

## Open questions for Darren
- Paste the exact error line from the old attempt's debug when you find it.
- Is the test server's spawn limit set to 2 or more for split-screen?
