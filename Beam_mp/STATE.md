# kkLap STATE  (live file: read this first, update it last)

Updated: 2026-10-26 by Claude. Branch: claude/blissful-mccarthy-eg6jed

## Goal (Darren, chat 26/10)
A far better lap timer than the built-in one, built so later game modes (drag, pursuit,
derby...) bolt onto the same server/client plumbing. Split-screen is NOT the focus;
keying everything by vehicle id makes it work anyway at no cost, so that stays.

## Where we are
Skeleton written, syntax-checked, zipped. **Never run on a real server yet.**
Darren has previously had a server plugin load and print to the console (chat 26/10),
so the Resources/Server path and onInit are known to work on his setup.
Next move is the first live load on the Linux test server via Pterodactyl.

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

## First live test, in order
1. Pterodactyl: upload Resources/Server/kkLap/main.lua and Resources/Client/kkLap.zip
   to the matching folders. Restart server.
2. Console should show: [KKLAP][I] kkLap loaded on Linux   (if not: casing or path)
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
