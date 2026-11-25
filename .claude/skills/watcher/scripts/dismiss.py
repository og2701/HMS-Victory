"""Runs on the VM (piped over ssh). argv: message_id [message_id ...] [workshop|testers|cabinet|dm|<channel_id>].

For hits that turn out not to be for Vic (people talking to each other): takes Vic's ACK reaction off and ends the
typing that the watcher started for them, so a message Vic isn't answering shows no sign of Vic (Owen, 25 Nov 2026).
Hits from one batch share a typing loop (watch.py writes <id>.alias files); the loop is only ended once every hit in
it has been dismissed or answered, so a real request in the same batch keeps its "typing"."""
import glob, os, signal, sys, urllib.error, urllib.parse, urllib.request
from dotenv import load_dotenv

load_dotenv(".env")
CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640", "dm": "1231209765754110002",
            "testers": "1570063729947844714"}
ACKS = ("vic_working:1575137733268078672", "hmmnote:1179638366275383297")  # current ACK, and the one before 25 Nov
DIR = "/tmp/vic_typing"

args = sys.argv[1:]
channel = next((CHANNELS.get(a, a) for a in args if a in CHANNELS or (a.isdigit() and len(a) < 21 and a in CHANNELS.values())),
               CHANNELS["workshop"])
ids = [a for a in args if a.isdigit() and a not in CHANNELS.values()]


def unreact(mid, emoji):
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel}/messages/{mid}/reactions/{urllib.parse.quote(emoji)}/@me",
        method="DELETE", headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
    try:
        urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError:
        pass


def loop_of(mid):
    try:
        return open(f"{DIR}/{mid}.alias").read().strip() or mid
    except OSError:
        return mid


for mid in ids:
    for emoji in ACKS:
        unreact(mid, emoji)
    loop = loop_of(mid)
    os.makedirs(DIR, exist_ok=True)
    open(f"{DIR}/{mid}.dismissed", "w").close()
    members = {loop} | {os.path.basename(a)[:-6] for a in glob.glob(f"{DIR}/*.alias")
                        if open(a).read().strip() == loop}
    live = [m for m in members if not os.path.exists(f"{DIR}/{m}.dismissed")]
    if not live:
        try:
            os.kill(int(open(f"{DIR}/{loop}.pid").read()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        for f in [f"{DIR}/{loop}.pid"] + [f"{DIR}/{m}.alias" for m in members] + [f"{DIR}/{m}.dismissed" for m in members]:
            try:
                os.remove(f)
            except OSError:
                pass
    print("dismissed", mid, "typing stopped" if not live else f"typing kept for {len(live)} other request(s)")
