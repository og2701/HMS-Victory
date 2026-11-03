"""Runs on the VM (piped over ssh). argv: the message id to start after, or "latest" for the newest one.

Polls #cabinet and exits when anyone (not a bot) pings Vic, replies to Vic or says "vic". Starts Vic typing straight
away (typing_indicator.py stop or the next card post ends it), then waits 30s so Vic's own auto-reply and any
follow-ups land, and prints the hits, a few messages of context before them, everything posted after them (Vic's
auto-reply included) and the last id seen. Gives up after 6 hours with "timeout": true."""
import json, os, re, signal, subprocess, sys, time, urllib.request
from dotenv import load_dotenv

load_dotenv(".env")
CH, VIC = 1155637791917613147, "1171842947440967770"
after = sys.argv[1]
deadline = time.time() + 6 * 3600


def get(path):
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


if after == "latest":
    after = get(f"/channels/{CH}/messages?limit=1")[0]["id"]


def wanted(m):
    if m["author"].get("bot"):
        return False
    ref = (m.get("referenced_message") or {}).get("author", {}).get("id")
    return bool(ref == VIC or any(u["id"] == VIC for u in m.get("mentions", []))
                or re.search(r"\bvic\b", m["content"] or "", re.I))


TYPING_LOOP = r'''
import os, time, urllib.request
req = lambda: urllib.request.Request("https://discord.com/api/v10/channels/1155637791917613147/typing", data=b"", method="POST",
    headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
end = time.time() + 600
while time.time() < end:
    try:
        urllib.request.urlopen(req(), timeout=10)
    except Exception:
        pass
    time.sleep(8)
'''


def start_typing():
    try:
        os.kill(int(open("/tmp/vic_typing.pid").read()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    p = subprocess.Popen([sys.executable, "-c", TYPING_LOOP], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open("/tmp/vic_typing.pid", "w").write(str(p.pid))


def brief(m):
    return {"id": m["id"], "author": m["author"].get("global_name") or m["author"]["username"], "bot": bool(m["author"].get("bot")),
            "content": m["content"], "attachments": [x["url"] for x in m["attachments"]],
            "reply_to": (m.get("referenced_message") or {}).get("id")}


while time.time() < deadline:
    try:
        msgs = sorted(get(f"/channels/{CH}/messages?after={after}&limit=50"), key=lambda m: int(m["id"]))
    except Exception as e:  # network blips just retry
        print("poll error", e, file=sys.stderr)
        time.sleep(30)
        continue
    hits = []
    for m in msgs:
        after = m["id"]
        if wanted(m):
            hits.append(brief(m))
    if hits:
        start_typing()
        time.sleep(30)
        before = sorted(get(f"/channels/{CH}/messages?before={hits[0]['id']}&limit=6"), key=lambda m: int(m["id"]))
        later = sorted(get(f"/channels/{CH}/messages?after={hits[0]['id']}&limit=50"), key=lambda m: int(m["id"]))
        if later:
            after = later[-1]["id"]
        print(json.dumps({"hits": hits, "context_before": [brief(m) for m in before],
                          "after_first_hit": [brief(m) for m in later], "last_id": after}))
        sys.exit(0)
    time.sleep(15)
print(json.dumps({"hits": [], "last_id": after, "timeout": True}))
