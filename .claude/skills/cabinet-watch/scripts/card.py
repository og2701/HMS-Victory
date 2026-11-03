"""Local helper. Turns a card file into a script that runs on the VM and posts it in #cabinet as Vic, as a
Components V2 card, with no pings, then stops the typing indicator. Pipe its output to the VM's venv python.

  vic_card.py <card_file> reply <message_id>   reply to a message
  vic_card.py <card_file> edit <message_id>    turn one of Vic's messages into this card
  vic_card.py <card_file> post                 plain post

Card file: first line 'accent: RRGGBB', then text. A line of '===' starts a new section (a separator is drawn
between sections). Use '## ' for the title, '### ' for section heads and '-# ' for small print."""
import json, sys

src, mode = sys.argv[1], sys.argv[2]
target = sys.argv[3] if len(sys.argv) > 3 else None
lines = open(src).read().strip().splitlines()
assert lines[0].startswith("accent:")
accent = int(lines[0].split(":", 1)[1].strip().lstrip("#"), 16)
sections, cur = [], []
for line in lines[1:]:
    if line.strip() == "===":
        sections.append("\n".join(cur).strip()); cur = []
    else:
        cur.append(line)
sections.append("\n".join(cur).strip())
sections = [s for s in sections if s]
text = "".join(sections)
assert len(text) <= 3800, len(text)
assert "—" not in text and "–" not in text, "no em/en dashes"

items = []
for i, s in enumerate(sections):
    if i:
        items.append({"type": 14, "divider": True, "spacing": 1})
    items.append({"type": 10, "content": s})
payload = {"flags": 1 << 15, "components": [{"type": 17, "accent_color": accent, "components": items}],
           "allowed_mentions": {"parse": [], "replied_user": False}}
method, path = "POST", "/channels/1155637791917613147/messages"
if mode == "reply":
    payload["message_reference"] = {"message_id": target, "fail_if_not_exists": False}
elif mode == "edit":
    payload["content"] = ""
    method, path = "PATCH", f"/channels/1155637791917613147/messages/{target}"

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
