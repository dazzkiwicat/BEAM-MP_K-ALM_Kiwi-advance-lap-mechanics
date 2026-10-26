# kkLap STATE  (live file: read this first, update it last)

Updated: 2026-10-26 by Claude. Branch: claude/blissful-mccarthy-eg6jed

## Where we are
Skeleton written, syntax-checked, zipped. **Never run on a real server yet.**
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

## UNCHECKED until the first live run
- onInit fires without MP.RegisterEvent (BeamMP's own example does it that way).
- onPlayerJoin exists (listed in docs join order, no section). Client asks for config
  itself on load (kkLap:getcfg) so nothing depends on it.
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

## Open questions for Darren
- Paste the exact error line from the old attempt's debug when you find it.
- Is the test server's spawn limit set to 2 or more for split-screen?
