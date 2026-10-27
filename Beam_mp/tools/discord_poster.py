#!/usr/bin/env python3
"""kkLap -> Discord: post new laps.log lines to a Discord channel.

The BeamMP server cannot call out (its Lua API has no HTTP), so this runs anywhere else:
it reads laps.log through the Pterodactyl client API and posts the lines it has not seen
yet to a Discord webhook, in one message per run. A cursor file remembers where it got to.

Environment (never in chat, never in git):
  PTERO_URL         https://panel.example.com
  PTERO_API_KEY     a client API key from the panel (Account > API Credentials)
  PTERO_SERVER_ID   the short id in the panel URL for the BeamMP server
  DISCORD_WEBHOOK   https://discord.com/api/webhooks/...
Dry run, no secrets needed:
  python3 tools/discord_poster.py --local path/to/laps.log --dry-run
"""
import argparse, json, os, pathlib, sys, urllib.request

LOG_PATH = "/Resources/Server/kkLap/laps.log"      # path inside the server container
STATE    = pathlib.Path(os.environ.get("KKLAP_POSTER_STATE", pathlib.Path.home() / ".kklap_poster.json"))

def fetch_log_from_panel():
    url = os.environ["PTERO_URL"].rstrip("/")
    key = os.environ["PTERO_API_KEY"]
    sid = os.environ["PTERO_SERVER_ID"]
    req = urllib.request.Request(f"{url}/api/client/servers/{sid}/files/contents?file={LOG_PATH}",
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")

def post(webhook, text):
    data = json.dumps({"content": text, "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json", "User-Agent": "kkLap-poster"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", help="read this laps.log instead of the panel")
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting")
    a = ap.parse_args()

    text  = pathlib.Path(a.local).read_text() if a.local else fetch_log_from_panel()
    lines = [l for l in text.splitlines() if l.strip()]
    state = json.loads(STATE.read_text()) if STATE.exists() else {"seen": 0}
    seen  = state.get("seen", 0)
    if len(lines) < seen:            # log was reset (new server, /clear): start over
        seen = 0
    new = lines[seen:]
    if not new:
        print("nothing new (%d lines seen)" % seen); return 0

    # laps.log lines look like: 2026-10-27 11:05:36 Darren  lap 1  0:05.100  [0.2 | 4.9]  best 0:05.100  @100 km/h  resets 0  race 2
    body = "**kkLap results**\n```\n" + "\n".join(l[11:] for l in new[-25:]) + "\n```"   # drop the date, keep the time
    if len(new) > 25: body += "(+%d more)" % (len(new) - 25)
    if a.dry_run:
        print(body)
    else:
        status = post(os.environ["DISCORD_WEBHOOK"], body)
        print("posted %d lines, discord said %s" % (len(new), status))
    STATE.write_text(json.dumps({"seen": len(lines)}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
