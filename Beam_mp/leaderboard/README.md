# kkLap leaderboard: setting up the web page

A web page that shows the lap board. It reads one file the mod already writes, `times.json`,
and refreshes every 30 seconds. Nothing on the game server changes. The page can only read,
never write.

**You need:** shell access to the machine that runs the Pterodactyl panel (ssh), Docker already
on it (Pterodactyl installs it), and about ten minutes.

## How it works, in one picture

```
BeamMP server  --writes-->  Resources/Server/kkLap/times.json
                                      |
                              read-only mount
                                      v
                    Caddy container (tiny web server, port 8080)
                                      |
                           serves index.html + times.json
                                      v
                       browser: http://<your host ip>:8080/
```

## Step 1: find the game server's folder on disk

Pterodactyl keeps each server's files under `/var/lib/pterodactyl/volumes/<uuid>/`.
The uuid is on the server's **Settings** tab in the panel, under "Server ID" or "UUID". It is the
long form with dashes, like `3f2a9c1e-....`, not the 8-character short id.

```
ls /var/lib/pterodactyl/volumes/<uuid>/Resources/Server/kkLap/
```

Check: you see `times.json`, `config.json`, `laps.log`, `kkLap.log`. If `times.json` is missing,
nobody has set a lap yet; it appears after the first counted lap.

## Step 2: put the page in place

Copy `index.html` from this folder (in the repo at `Beam_mp/leaderboard/index.html`) onto the host:

```
sudo mkdir -p /opt/kklap-web
sudo cp index.html /opt/kklap-web/index.html
```

Check: `ls -l /opt/kklap-web/` shows one file.

## Step 3: start the web server

One container, restarts itself with the machine, mounts the two files read-only.
Replace `<uuid>` with the one from step 1.

```
sudo docker run -d --name kklap-web --restart unless-stopped -p 8080:80 \
  -v /opt/kklap-web/index.html:/srv/index.html:ro \
  -v /var/lib/pterodactyl/volumes/<uuid>/Resources/Server/kkLap/times.json:/srv/times.json:ro \
  caddy:2 caddy file-server --root /srv
```

Check:

```
curl -s http://localhost:8080/times.json | head -c 200
```

prints JSON starting with `{`. Then open `http://<host ip>:8080/` in a browser. You see the board.

## Step 4: let people reach it

Open TCP port 8080 on the host firewall (`ufw allow 8080/tcp` if ufw, otherwise your provider's
panel). Share `http://<host ip>:8080/` in Discord.

If the host already has a domain and a reverse proxy in front of the panel, point a subdomain at
port 8080 instead and skip the firewall change. Ask Darren for the Caddyfile if you want HTTPS;
Caddy does the certificate on its own.

## Keeping it running

- **It stopped showing new laps.** Docker mounts a single file by its inode. The mod rewrites
  `times.json` in place, which keeps the inode, so this is rare. If the file was ever deleted and
  recreated (a `lap clear`, or a fresh install), run `sudo docker restart kklap-web` once.
- **Update the page.** Copy the new `index.html` over the old one and `sudo docker restart kklap-web`.
- **Remove it.** `sudo docker rm -f kklap-web`. Nothing else was changed.

## Why it is safe

- The two mounts end in `:ro`. The container cannot write to the game server.
- Only `index.html` and `times.json` are served. `config.json` (admin names), `laps.log` and the
  BeamMP config stay unreachable. Do not widen the mount to the whole folder.
- `times.json` holds player names, best laps, sector times and speeds. Nothing else.

## What the page shows

Rank, driver, best lap, laps counted, speed at the line, physics resets, and points. Points use the
mod's rule: 100 for the top time, less the further off it you are, shown once three drivers have a
time. The top row is highlighted.
