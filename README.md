kkLap - BeamMP lap timer that works with 0.40 split-screen (Linux server)
Server plugin + client mod. Server keeps the clock and the checkpoints; clients only report "my vehicle entered checkpoint N". Split-screen is one client with two vehicles, so every message carries the vehicle id.


In game

/lap mark          drop a checkpoint at your car. First one is start/finish.
/lap list          show them
/lap undo          remove the last one
/lap clear         remove all
/lap reset         wipe timing, keep checkpoints
/lap race [sec]    countdown for everyone, GO starts every car in the start bubble (v0.6.0)
/lap autostart on|off  a honk inside the start bubble starts that countdown (default on)
/lap set countdown 5
/lap times         best lap per player with sector times, top 10 (v0.5.0)
/lap announce on|off  every sector to everyone in chat (default off; laps always announce)
/lap markers on|off  draw the checkpoint spheres in the world (default on, v0.4.0)
/lap debug on|off  forward client debug lines to the server log
/lap set minLapMs 5000
/lap set markerAlpha 40   see-through-ness of the markers, 0 invisible, 255 solid (v0.5.2)
