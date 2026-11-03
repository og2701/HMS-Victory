"""Runs on the VM (piped over ssh). argv: message_id emoji [cabinet|workshop] [keep] [remove].

Adds Vic's reaction to a message (#cabinet by default). The emoji is a normal one like ✅, or a server emoji as
name:id. A reaction usually is the whole answer, so it stops the typing indicator unless "keep" is given (for a 👀
while a longer reply is being written). "remove" takes Vic's reaction off instead."""
import os, signal, sys, urllib.error, urllib.parse, urllib.request
from dotenv import load_dotenv

load_dotenv(".env")
CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640"}
message_id, emoji, flags = sys.argv[1], sys.argv[2], sys.argv[3:]
channel = CHANNELS[next((f for f in flags if f in CHANNELS), "cabinet")]
method = "DELETE" if "remove" in flags else "PUT"
req = urllib.request.Request(
    f"https://discord.com/api/v10/channels/{channel}/messages/{message_id}/reactions/{urllib.parse.quote(emoji)}/@me",
    method=method, headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
try:
    urllib.request.urlopen(req, timeout=20)
    print("removed" if method == "DELETE" else "reacted", emoji)
except urllib.error.HTTPError as e:
    print("failed", e.code, e.read().decode()[:300])
if "keep" not in flags:
    try:
        os.kill(int(open("/tmp/vic_typing.pid").read()), signal.SIGTERM)
        os.remove("/tmp/vic_typing.pid")
    except (OSError, ValueError):
        pass
