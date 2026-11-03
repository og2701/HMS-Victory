"""Runs on the VM (piped over ssh). argv: before_id [count] [cabinet|workshop]. Prints that channel's messages
(#cabinet by default) before that id, oldest first, one per line, for when an ask refers back further than the
watcher's context. count defaults to 100 (max 500)."""
import json, os, sys, urllib.request
from dotenv import load_dotenv

load_dotenv(".env")
CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640"}
before, want = sys.argv[1], min(int(sys.argv[2]) if len(sys.argv) > 2 else 100, 500)
CH = CHANNELS[sys.argv[3] if len(sys.argv) > 3 else "cabinet"]


def get(path):
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def text_of(m):
    """Message text, including the text inside Components V2 cards (Vic's replies)."""
    parts = [m["content"]] if m.get("content") else []
    stack = list(m.get("components") or [])
    while stack:
        c = stack.pop(0)
        if c.get("type") == 10:
            parts.append(c.get("content", ""))
        stack[:0] = c.get("components") or []
    return "\n".join(parts)


msgs = []
while len(msgs) < want:
    page = get(f"/channels/{CH}/messages?before={before}&limit={min(100, want - len(msgs))}")
    if not page:
        break
    msgs += page
    before = page[-1]["id"]
for m in sorted(msgs, key=lambda m: int(m["id"])):
    who = m["author"].get("global_name") or m["author"]["username"]
    reply = f" (reply to {m['referenced_message']['id']})" if m.get("referenced_message") else ""
    files = f" [{len(m['attachments'])} attachment(s)]" if m["attachments"] else ""
    text = text_of(m).replace("\n", " / ")
    print(f"{m['id']} {who}{reply}: {text}{files}")
