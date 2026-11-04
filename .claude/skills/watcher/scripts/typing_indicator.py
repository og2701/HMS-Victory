"""Runs on the VM (piped over ssh). argv: start <workshop|cabinet|dm> [request_id] | stop [request_id].

start keeps Vic 'typing' in that channel for one request (Discord's indicator lasts ~10s per call, so it re-sends
every 8s) in a detached process for up to 10 minutes. Each request gets its own loop, keyed by the request's message
id, so parallel jobs never cut each other's typing off: stop <request_id> ends that one, a plain stop ends them all.
card.py replies and react.py reactions end the loop for the message they answer."""
import glob, os, signal, subprocess, sys
from dotenv import load_dotenv

load_dotenv(".env")
CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640", "dm": "1231209765754110002"}  # dm: Vic and Oggers
DIR = "/tmp/vic_typing"
LOOP = r'''
import os, sys, time, urllib.request
req = lambda: urllib.request.Request(f"https://discord.com/api/v10/channels/{sys.argv[1]}/typing", data=b"", method="POST",
    headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
end = time.time() + 600
while time.time() < end:
    try:
        urllib.request.urlopen(req(), timeout=10)
    except Exception:
        pass
    time.sleep(8)
'''


def stop(request_id=None):
    files = glob.glob(f"{DIR}/*.pid") + ["/tmp/vic_typing.pid"] if request_id is None else [f"{DIR}/{request_id}.pid"]
    for f in files:
        try:
            os.kill(int(open(f).read()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        try:
            os.remove(f)
        except OSError:
            pass


def start(channel="workshop", request_id="manual"):
    stop(request_id)
    os.makedirs(DIR, exist_ok=True)
    p = subprocess.Popen([sys.executable, "-c", LOOP, CHANNELS[channel]], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open(f"{DIR}/{request_id}.pid", "w").write(str(p.pid))


if __name__ == "__main__":
    {"start": start, "stop": stop}[sys.argv[1]](*sys.argv[2:4])
    print("typing", *sys.argv[1:4])
