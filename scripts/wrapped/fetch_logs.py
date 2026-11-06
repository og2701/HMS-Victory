"""Wick's mod log and Ticket Tool's closed-ticket records, re-read through the bot's token on the VM.

The chronicle keeps every bot message but not what's inside its embeds, and both of these bots post
nothing but embeds. Discord's own audit log only goes back 45 days, so these are the only record of
the year's moderation before the chronicle's audit log starts. Read-only: it only fetches history.
Prints {"logs": [...], "usernames": {username: id}}, the second because Wick names moderators by
username.

    ssh vm 'cd ~/HMS-Victory && venv/bin/python - 2026-01-01' < scripts/wrapped/fetch_logs.py > mod_logs.json
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv

API = "https://discord.com/api/v10"
GUILD = 959493056242008184
CHANNELS = {"police-station": 1132970233502650388, "ticket-archive": 1143561611831943248}
BOTS = {536991182035746816: "wick", 557628352828014614: "ticket-tool"}

load_dotenv(os.path.join(os.getcwd(), ".env"))
HEADERS = {"Authorization": f"Bot {os.environ['DISCORD_TOKEN']}", "User-Agent": "DiscordBot (hms-victory, 1)"}


def get(path):
    for _ in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(API + path, headers=HEADERS)))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.load(e).get("retry_after", 1)) + 0.2)
                continue
            if e.code in (403, 404):
                return None
            raise
    return None


since = datetime.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-01-01").replace(tzinfo=timezone.utc)
after = (int(since.timestamp() * 1000) - 1420070400000) << 22  # the snowflake for that moment
out = []
for name, cid in CHANNELS.items():
    cursor = after
    while True:
        page = get(f"/channels/{cid}/messages?limit=100&after={cursor}") or []
        for m in page:
            bot = BOTS.get(int(m["author"]["id"]))
            if not bot:
                continue
            out.append({"id": m["id"], "ts": m["timestamp"], "channel": name, "bot": bot, "content": m.get("content", ""),
                        "embeds": [{k: e.get(k) for k in ("title", "description", "fields", "author", "footer")}
                                   for e in m.get("embeds", [])],
                        "attachments": [a.get("filename") for a in m.get("attachments", [])]})
        if len(page) < 100:
            break
        cursor = max(int(m["id"]) for m in page)
        time.sleep(0.4)
usernames, cursor = {}, 0
while True:
    page = get(f"/guilds/{GUILD}/members?limit=1000&after={cursor}") or []
    for m in page:
        usernames[m["user"]["username"]] = m["user"]["id"]
    if len(page) < 1000:
        break
    cursor = page[-1]["user"]["id"]
    time.sleep(0.5)
json.dump({"logs": out, "usernames": usernames}, sys.stdout)
