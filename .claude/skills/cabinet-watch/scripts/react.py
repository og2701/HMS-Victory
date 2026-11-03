"""Runs on the VM (piped over ssh). argv: message_id emoji [cabinet|workshop] [keep] [remove].

Adds Vic's reaction to a message (#cabinet by default). The emoji is a normal one like ✅, or a server emoji as
name:id; emojis.py lists the server's. Timeout, flag and cross emojis are refused. A reaction usually is the whole
answer, so it stops the typing indicator and takes off the watcher's ACK unless "keep" is given (for a reaction
while a longer reply is being written). "remove" takes Vic's reaction off instead."""
import os, signal, sys, urllib.error, urllib.parse, urllib.request
from dotenv import load_dotenv

load_dotenv(".env")
CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640"}
# Reactions that set off Vic's own reaction features: timeouts (Shut, bedtime, oggersglare), translations (flags)
# and deleting Vic's posts (the cross). Vic must never use them.
BLOCKED_NAMES = {"shut", "bedtime", "oggersglare"}
ACK = "hmmnote:1179638366275383297"  # watch.py's "looking into it"


def blocked(emoji):
    name = emoji.split(":")[0].lower()
    flag = any(0x1F1E6 <= ord(c) <= 0x1F1FF or 0xE0000 <= ord(c) <= 0xE007F for c in emoji) or "\U0001F3F4" in emoji
    return name in BLOCKED_NAMES or flag or emoji in ("\u274c", "\u274e", "\u2716\ufe0f", "\u2716")


message_id, emoji, flags = sys.argv[1], sys.argv[2], sys.argv[3:]
if blocked(emoji) and "remove" not in flags:
    sys.exit(f"refused: {emoji} sets off one of Vic's reaction features (timeouts, translation or deleting)")
channel = CHANNELS[next((f for f in flags if f in CHANNELS), "cabinet")]
method = "DELETE" if "remove" in flags else "PUT"


def reaction(method, emoji):
    return urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel}/messages/{message_id}/reactions/{urllib.parse.quote(emoji)}/@me",
        data=b"" if method == "PUT" else None, method=method,
        headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})


try:
    urllib.request.urlopen(reaction(method, emoji), timeout=20)
    print("removed" if method == "DELETE" else "reacted", emoji)
except urllib.error.HTTPError as e:
    print("failed", e.code, e.read().decode()[:300])
if method == "PUT" and emoji != ACK and "keep" not in flags:
    try:  # the answer is in, so "looking into it" comes off
        urllib.request.urlopen(reaction("DELETE", ACK), timeout=20)
    except urllib.error.HTTPError:
        pass
if "keep" not in flags:
    try:
        os.kill(int(open("/tmp/vic_typing.pid").read()), signal.SIGTERM)
        os.remove("/tmp/vic_typing.pid")
    except (OSError, ValueError):
        pass
