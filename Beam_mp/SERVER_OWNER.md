# kkLap: what the server owner does

kkLap is a lap timer for the BeamMP server. Players park in a start bubble, honk, get a
countdown, and their laps, sectors and best times are kept on the server. Admin work happens in
the **server console** (the Console tab in the panel), not in game chat. This page is everything
you need at your end. Darren does the rest.

## Install once

Two files from the repo, into the server's `Resources` folder:

| Repo file | Goes to |
|---|---|
| `Beam_mp/Resources/Server/kkLap/main.lua` | `Resources/Server/kkLap/main.lua` |
| `Beam_mp/Resources/Client/kkLap_v0.25.zip` | `Resources/Client/kkLap_v0.25.zip` |

Then **restart the server**. Check: the console prints `kkLap v0.25 loaded on Linux`.
The folder `Resources/Server/kkLap/` fills itself with `config.json`, `times.json`,
`laps.log` and `kkLap.log`. Leave them there.

Music is a third file, optional, from Darren, not from the repo:
`kkLap_music.zip` goes into `Resources/Client/` beside the code zip. About 112 MB, so the first
join takes longer while it downloads.

## Every update

1. Replace `Resources/Server/kkLap/main.lua` with the new one.
2. Put the new `kkLap_v0.XX.zip` in `Resources/Client/`, **delete the old one**. Two kkLap
   zips side by side means every player downloads both and the game loads whichever it finds first.
3. Restart.

Check: console says `kkLap v0.XX loaded` with the new number. Players who were on get told
in chat to rejoin if their client version differs from the server's.

## Console commands

Type at the prompt in the panel's Console tab. Every line starts with `lap`. The reply prints
in the same console and every command is written to `kkLap.log`.

| Line | Does |
|---|---|
| `lap help` | lists everything |
| `lap status` | race state, who is in the start bubble, where each driver is in their lap |
| `lap report` | writes `Resources/Server/kkLap/report.txt` with status, config, board and the last 300 log lines |
| `lap race stop` | cancels a countdown that is stuck |
| `lap set countdown 5` | seconds of countdown (1 to 60) |
| `lap set minLaps 2` | laps before a best time goes on the board |
| `lap music set.mp3 0.4` | track to autoplay for everyone, volume 0 to 10; `lap music stop` clears it |
| `lap hud off` / `lap hud on` | the on-screen timer and messages |
| `lap times` | the board |
| `lap reset` | drops every lap in progress; `lap clear` also wipes the board and the track |
| `lap adminchat on` | allows admin commands in game chat again (off by default) |

Marking the track needs a car on the spot, so Darren does that from the game, or from the
console with a player's name: `lap mark <playername>` uses that player's car position.

## Leaderboard web page

Optional. A static page reads `times.json` and shows the board. One Docker line, read-only, no
database. Instructions in `Beam_mp/leaderboard/README.md`. It needs the server's volume uuid
from the panel and a port opened on the host.

## When something goes wrong

Run `lap report` in the console and send Darren the file it names,
`Resources/Server/kkLap/report.txt`. That one file replaces the five we used to pass around.
If the console itself is dead, send `Resources/Server/kkLap/kkLap.log` and the BeamMP
`Server.log`.

Lines you can ignore in `Server.log`: `Invalid code got in Identify` (a client knocking with a
bad handshake) and the backend heartbeat timeout. Both predate the mod.

## Security, read once

- **Never send `ServerConfig.toml` to anyone.** It holds the server's AuthKey. One copy went into a
  chat on 30/10, so regenerate the key at keymaster.beammp.com and paste the new one into the
  panel. Takes a minute.
- `config.json` holds admin names only, nothing secret. `times.json` and `laps.log` hold player
  names and lap times, nothing else.
- The music file never goes in the GitHub repo. Only the code does.
