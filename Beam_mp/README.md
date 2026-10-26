# kkLap - BeamMP lap timer that works with 0.40 split-screen (Linux server)

Server plugin + client mod. Server keeps the clock and the checkpoints; clients only
report "my vehicle entered checkpoint N". Split-screen is one client with two vehicles,
so every message carries the vehicle id.

## Layout (drop straight onto the BeamMP server)
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
    /lap debug on|off  forward client debug lines to the server log
    /lap set minLapMs 5000

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

## What is BeamMP and what is BeamNG in the client file
BeamMP: TriggerServerEvent, AddEventHandler, MPVehicleGE.isOwn.
BeamNG game Lua: be:getObjectByID, be:getPlayerVehicle, veh:getPosition, guihooks.trigger, log, jsonEncode/jsonDecode.
Server file is 100% BeamMP server API (MP.*, Util.Json*).
