"""Local helper. Turns a card file into a script that runs on the VM and posts it as Vic, as a Components V2
card. A reply also takes the watcher's ACK reaction off the request and stops that request's typing; other jobs'
typing carries on. Pipe its output to the VM's venv python.

  card.py <card_file> reply <message_id> [channel]   reply to a message
  card.py <card_file> edit <message_id> [channel]    turn one of Vic's messages into this card
  card.py <card_file> post [channel]                 plain post

channel is workshop (the default), dm, cabinet, or the id of a channel the watcher is watching for a while.

Card file: first line 'accent: RRGGBB', then an optional 'ping: <user_id>' line, then text. A reply pings the
person it replies to (so they see the turn is done); add a 'quiet' line under the accent to reply without that.
Nobody else is pinged unless named on a ping line, and that person must be @'d as <@id> in the text. A line of '===' starts a new
section (a separator is drawn between sections). Use '## ' for the title, '### ' for section heads and '-# ' for
small print. An 'image: /path/to/file.png' line anywhere attaches that image (render.py makes them from HTML) and
shows it under the card, outside the bordered box so a framed chart isn't boxed twice. A card can be images only:
then the file is just 'accent:' plus image lines."""
import base64, json, os, sys, urllib.parse

CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640", "dm": "1231209765754110002"}  # dm: Vic and Oggers
ACK_QUOTED = urllib.parse.quote("hmmnote:1179638366275383297")  # watch.py's "looking into it"
src, mode, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
target = rest.pop(0) if mode in ("reply", "edit") else None
CH = CHANNELS.get(rest[0], rest[0]) if rest else CHANNELS["workshop"]
assert CH.isdigit(), f"unknown channel {CH}"
lines = open(src).read().strip().splitlines()
assert lines[0].startswith("accent:")
accent = int(lines[0].split(":", 1)[1].strip().lstrip("#"), 16)
body = lines[1:]
quiet = False
if body and body[0].strip() == "quiet":
    quiet = True
    body.pop(0)
ping = None
if body and body[0].startswith("ping:"):
    ping = body.pop(0).split(":", 1)[1].strip()
    assert ping.isdigit(), ping
images = [line.split(":", 1)[1].strip() for line in body if line.startswith("image:")]
body = [line for line in body if not line.startswith("image:")]
for path in images:
    assert os.path.getsize(path) < 9_000_000, f"{path} is too big for Discord"
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
names = [f"vic-{i}{os.path.splitext(p)[1].lower() or '.png'}" for i, p in enumerate(images)]
components = [{"type": 17, "accent_color": accent, "components": items}] if items else []
if images:
    components.append({"type": 12, "items": [{"media": {"url": f"attachment://{n}"}} for n in names]})
payload = {"flags": 1 << 15, "components": components,
           "allowed_mentions": {"parse": [], "users": [ping] if ping else [], "replied_user": mode == "reply" and not quiet},
           "attachments": [{"id": i, "filename": n} for i, n in enumerate(names)]}
files = {n: base64.b64encode(open(p, "rb").read()).decode() for n, p in zip(names, images)}
method, path = "POST", f"/channels/{CH}/messages"
if mode == "reply":
    payload["message_reference"] = {"message_id": target, "fail_if_not_exists": False}
elif mode == "edit":
    payload["content"] = ""
    method, path = "PATCH", f"/channels/{CH}/messages/{target}"

print(f'''import base64, json, os, signal, urllib.request, uuid
from dotenv import load_dotenv
load_dotenv(".env")
files = {json.dumps(files)}
boundary = uuid.uuid4().hex
body = (f"--{{boundary}}\\r\\nContent-Disposition: form-data; name=\\"payload_json\\"\\r\\n"
        f"Content-Type: application/json\\r\\n\\r\\n").encode() + {json.dumps(json.dumps(payload))}.encode() + b"\\r\\n"
for i, (name, data) in enumerate(files.items()):
    body += (f"--{{boundary}}\\r\\nContent-Disposition: form-data; name=\\"files[{{i}}]\\"; filename=\\"{{name}}\\"\\r\\n"
             f"Content-Type: image/png\\r\\n\\r\\n").encode() + base64.b64decode(data) + b"\\r\\n"
body += f"--{{boundary}}--\\r\\n".encode()
r = urllib.request.Request("https://discord.com/api/v10{path}", data=body, method="{method}",
    headers={{"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "Content-Type": f"multipart/form-data; boundary={{boundary}}",
             "User-Agent": "DiscordBot (x, 1)"}})
try:
    print("{mode}", json.load(urllib.request.urlopen(r))["id"])
except urllib.error.HTTPError as e:
    print("failed", e.code, e.read().decode()[:500])
if {mode == "reply"}:  # the answer is in, so the watcher's "looking into it" comes off the request
    try:
        urllib.request.urlopen(urllib.request.Request(
            "https://discord.com/api/v10/channels/{CH}/messages/{target}/reactions/{ACK_QUOTED}/@me", method="DELETE",
            headers={{"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"}}), timeout=20)
    except urllib.error.HTTPError:
        pass
    try:  # and only this request's typing stops; parallel jobs keep theirs
        os.kill(int(open("/tmp/vic_typing/{target}.pid").read()), signal.SIGTERM); os.remove("/tmp/vic_typing/{target}.pid")
    except (OSError, ValueError):
        pass
''')
