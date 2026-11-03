"""Local helper. Turns a card file into a script that runs on the VM and posts it as Vic, as a Components V2
card, then stops the typing indicator. Pipe its output to the VM's venv python.

  card.py <card_file> reply <message_id> [channel]   reply to a message
  card.py <card_file> edit <message_id> [channel]    turn one of Vic's messages into this card
  card.py <card_file> post [channel]                 plain post

channel is cabinet (the default) or workshop.

Card file: first line 'accent: RRGGBB', then an optional 'ping: <user_id>' line, then text. Nobody is pinged
unless named on a ping line, and that person must be @'d as <@id> in the text. A line of '===' starts a new
section (a separator is drawn between sections). Use '## ' for the title, '### ' for section heads and '-# ' for
small print."""
import json, sys

CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640"}
src, mode, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
target = rest.pop(0) if mode in ("reply", "edit") else None
CH = CHANNELS[rest[0] if rest else "cabinet"]
lines = open(src).read().strip().splitlines()
assert lines[0].startswith("accent:")
accent = int(lines[0].split(":", 1)[1].strip().lstrip("#"), 16)
body = lines[1:]
ping = None
if body and body[0].startswith("ping:"):
    ping = body.pop(0).split(":", 1)[1].strip()
    assert ping.isdigit(), ping
sections, cur = [], []
for line in body:
    if line.strip() == "===":
        sections.append("\n".join(cur).strip()); cur = []
    else:
        cur.append(line)
sections.append("\n".join(cur).strip())
sections = [s for s in sections if s]
text = "".join(sections)
assert len(text) <= 3800, len(text)
assert "\u2014" not in text and "\u2013" not in text, "no em/en dashes"
assert not ping or f"<@{ping}>" in text, "the pinged person must be @'d in the text"

items = []
for i, s in enumerate(sections):
    if i:
        items.append({"type": 14, "divider": True, "spacing": 1})
    items.append({"type": 10, "content": s})
payload = {"flags": 1 << 15, "components": [{"type": 17, "accent_color": accent, "components": items}],
           "allowed_mentions": {"parse": [], "users": [ping] if ping else [], "replied_user": False}}
method, path = "POST", f"/channels/{CH}/messages"
if mode == "reply":
    payload["message_reference"] = {"message_id": target, "fail_if_not_exists": False}
elif mode == "edit":
    payload["content"] = ""
    method, path = "PATCH", f"/channels/{CH}/messages/{target}"

print(f'''import json, os, signal, urllib.request
from dotenv import load_dotenv
load_dotenv(".env")
r = urllib.request.Request("https://discord.com/api/v10{path}", data={json.dumps(json.dumps(payload))}.encode(), method="{method}",
    headers={{"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "Content-Type": "application/json", "User-Agent": "DiscordBot (x, 1)"}})
try:
    print("{mode}", json.load(urllib.request.urlopen(r))["id"])
except urllib.error.HTTPError as e:
    print("failed", e.code, e.read().decode()[:500])
try:
    os.kill(int(open("/tmp/vic_typing.pid").read()), signal.SIGTERM); os.remove("/tmp/vic_typing.pid")
except (OSError, ValueError):
    pass
''')
