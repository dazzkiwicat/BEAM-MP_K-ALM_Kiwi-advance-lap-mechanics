# kkLap web leaderboard

One static page that reads the mod's own `times.json`. No database, no code added to the game
server, nothing can write back. Three ways to host it, easiest first.

## What it needs
- `index.html` from this folder.
- `times.json`, which kkLap rewrites after every counted lap at
  `Resources/Server/kkLap/times.json` inside the BeamMP server's folder.
The page fetches `times.json` from the same folder as itself every 30 seconds, so both files must
be served from one place. Points use the same rule as the server (100 for the top time, less the
further off you are, shown once 3 drivers have a time).

## Option A: Caddy container on the same machine (recommended)
Pterodactyl keeps each game server's files on the host at
`/var/lib/pterodactyl/volumes/<server uuid>/`. The uuid is on the server's Settings page in the panel
(the long one, not the 8-character short id). Mount the kkLap folder read-only and let Caddy serve it.

```
sudo mkdir -p /opt/kklap-web
sudo cp index.html /opt/kklap-web/
sudo docker run -d --name kklap-web --restart unless-stopped -p 8080:80 \
  -v /opt/kklap-web/index.html:/srv/index.html:ro \
  -v /var/lib/pterodactyl/volumes/<server uuid>/Resources/Server/kkLap/times.json:/srv/times.json:ro \
  caddy:2 caddy file-server --root /srv
```

Check: `curl -s http://localhost:8080/times.json | head -c 200` prints JSON. Then open
`http://<host ip>:8080/` in a browser. Open port 8080 in the host firewall or put it behind the
reverse proxy already in front of the panel (Caddy/nginx), whichever the owner runs.

Trap: `:ro` on the mount is the whole safety story. The page can never write, and neither can
anyone who finds the port. Keep it.

Trap 2: Docker bind-mounts a single file by inode. kkLap rewrites `times.json` in place
(open "w", write, close), which keeps the inode, so the mount keeps following it. If the file is
ever deleted and recreated, restart the container once (`docker restart kklap-web`). Mounting the
whole folder instead (`.../kkLap:/srv/data:ro`, then fetch `data/times.json`) avoids that, but also
exposes `kkLap.json` (the config, admin names) and `laps.log`. Pick the single-file mount.

## Option B: a second Pterodactyl "server" running a static web egg
If the owner prefers everything in the panel: make a tiny server from a static-site or nginx egg,
upload `index.html`, and have the kkLap server's `times.json` copied in by a cron job on the host
every minute (`cp` from one volume to the other). Works, but it is a copy with a one-minute lag and
one more thing to break. Only if Option A is not allowed.

## Option C: GitHub Pages fed by the Discord poster
`tools/discord_poster.py` already reads the server over the panel API. A sibling script could pull
`times.json` the same way and commit it to a `gh-pages` branch, with `index.html` beside it. Free
hosting, public URL, no ports on the game host. Lag = however often the script runs. Not written yet.

## HTTPS
Not needed for a read-only board on a LAN or Discord-shared IP. If it goes behind the owner's
domain, Caddy does the certificate on its own: replace the `caddy file-server` line with a Caddyfile
that names the domain. Ask and I'll write that one.
