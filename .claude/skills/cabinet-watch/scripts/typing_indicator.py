"""Runs on the VM (piped over ssh). argv: start | stop. start keeps Vic 'typing' in #cabinet (Discord's indicator
lasts ~10s per call, so it re-sends every 8s) in a detached process for up to 10 minutes; stop ends it."""
import os, signal, subprocess, sys
from dotenv import load_dotenv

load_dotenv(".env")
PID = "/tmp/vic_typing.pid"
LOOP = r'''
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


def stop():
    try:
        os.kill(int(open(PID).read()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    try:
        os.remove(PID)
    except OSError:
        pass


def start():
    stop()
    p = subprocess.Popen([sys.executable, "-c", LOOP], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open(PID, "w").write(str(p.pid))


if __name__ == "__main__":
    {"start": start, "stop": stop}[sys.argv[1]]()
    print("typing", sys.argv[1])
