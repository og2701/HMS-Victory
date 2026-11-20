"""Names, avatars, channels and emoji for Wrapped, read through the bot's token on the VM.

Runs on the bot's box (pull.sh pipes it over ssh) and prints one JSON object:

    ssh vm 'cd ~/HMS-Victory && venv/bin/python - [user ids...]' < scripts/wrapped/fetch_discord.py

Members come from the guild member list. Any user ids passed as arguments that aren't members any more
(people who have left) are looked up one by one, so the video can still show their name and face.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

GUILD = 959493056242008184
API = "https://discord.com/api/v10"
CDN = "https://cdn.discordapp.com"

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
            if e.code == 404:
                return None
            raise
    return None


def avatar(user, member=None):
    if member and member.get("avatar"):
        h = member["avatar"]
        return f"{CDN}/guilds/{GUILD}/users/{user['id']}/avatars/{h}.png?size=256"
    if user.get("avatar"):
        return f"{CDN}/avatars/{user['id']}/{user['avatar']}.png?size=256"
    return f"{CDN}/embed/avatars/{(int(user['id']) >> 22) % 6}.png"


people, after = {}, 0
while True:
    page = get(f"/guilds/{GUILD}/members?limit=1000&after={after}") or []
    for m in page:
        u = m["user"]
        people[u["id"]] = {"name": m.get("nick") or u.get("global_name") or u["username"],
                           "avatar": avatar(u, m), "bot": bool(u.get("bot")), "member": True}
    if len(page) < 1000:
        break
    after = page[-1]["user"]["id"]
    time.sleep(0.5)

for uid in sys.argv[1:]:
    if uid in people:
        continue
    u = get(f"/users/{uid}")
    if u:
        people[uid] = {"name": u.get("global_name") or u["username"], "avatar": avatar(u),
                       "bot": bool(u.get("bot")), "member": False}
    time.sleep(0.05)

guild = get(f"/guilds/{GUILD}") or {}
channels = {c["id"]: {"name": c["name"], "type": c["type"], "parent": c.get("parent_id")}
            for c in get(f"/guilds/{GUILD}/channels") or []}
for t in (get(f"/guilds/{GUILD}/threads/active") or {}).get("threads", []):
    channels[t["id"]] = {"name": t["name"], "type": t["type"], "parent": t.get("parent_id")}
emojis = {e["id"]: {"name": e["name"], "animated": bool(e.get("animated"))} for e in guild.get("emojis", [])}

print(json.dumps({
    "guild": {"name": guild.get("name"),
              "icon": f"{CDN}/icons/{GUILD}/{guild['icon']}.png?size=512" if guild.get("icon") else None,
              "members": guild.get("approximate_member_count")},
    "people": people,
    "channels": channels,
    "emojis": emojis,
}))
