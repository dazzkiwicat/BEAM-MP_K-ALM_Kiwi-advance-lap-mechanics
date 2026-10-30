#!/usr/bin/env bash
# kkLap leaderboard: (re)create the web container for one game server.
# Usage:  sudo ./kklap-web.sh <server uuid> [port]
# Safe to rerun: removes the old container first. Nothing on the game server is touched.
set -euo pipefail
UUID="${1:?usage: kklap-web.sh <server uuid> [port]}"
PORT="${2:-8080}"
VOL="/var/lib/pterodactyl/volumes/$UUID/Resources/Server/kkLap"
WEB="/opt/kklap-web"
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -d "$VOL" ] || { echo "no folder at $VOL (wrong uuid, or kkLap never ran)"; exit 1; }
[ -f "$VOL/times.json" ] || echo "note: $VOL/times.json not there yet, it appears after the first counted lap; mounting anyway"
touch "$VOL/times.json" 2>/dev/null || true        # so the bind mount has a file to follow

mkdir -p "$WEB"
cp "$HERE/index.html" "$WEB/index.html"

docker rm -f kklap-web >/dev/null 2>&1 || true
docker run -d --name kklap-web --restart unless-stopped -p "$PORT:80" \
  -v "$WEB/index.html:/srv/index.html:ro" \
  -v "$VOL/times.json:/srv/times.json:ro" \
  caddy:2 caddy file-server --root /srv >/dev/null

sleep 1
if curl -fs "http://localhost:$PORT/times.json" | head -c 120; then
  echo; echo "ok: board at http://<host ip>:$PORT/"
else
  echo "container is up but times.json did not serve; try: ls -l $VOL/times.json  and  docker logs kklap-web"
fi
