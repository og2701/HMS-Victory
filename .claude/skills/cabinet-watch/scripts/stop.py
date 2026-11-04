"""Runs on the VM. argv: [all]. Stops the watcher and every request's typing loop. Killing the local ssh leaves the
remote python running, so this is how the watcher really ends.

Workers' own scripts (data pulls and the like) keep running, so a worker mid-job isn't cut off. "all" also stops
every other stdin-piped script (`venv/bin/python -`), never the bot itself (run.py)."""
import glob, os, signal, subprocess, sys

WATCH_PID = "/tmp/vic_watch.pid"
me = {os.getpid(), os.getppid()}  # this script and the ssh shell that launched it match the pattern too


def piped():
    out = subprocess.run(["pgrep", "-f", "venv/bin/python -"], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out if int(p) not in me]


def is_piped_python(pid):
    try:
        return open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")[:2] == [b"venv/bin/python", b"-"]
    except OSError:
        return False


try:
    pid = int(open(WATCH_PID).read())
    if is_piped_python(pid):  # a stale file could name a pid that's since been reused by something else
        os.kill(pid, signal.SIGTERM)
        print("stopped watcher", pid)
    os.remove(WATCH_PID)
except (OSError, ValueError):
    pass
if sys.argv[1:] == ["all"]:
    for pid in piped():
        os.kill(pid, signal.SIGTERM)
        print("stopped", pid)
for f in glob.glob("/tmp/vic_typing/*.pid") + ["/tmp/vic_typing.pid"]:
    try:
        os.kill(int(open(f).read()), signal.SIGTERM)
        os.remove(f)
        print("stopped typing", os.path.basename(f))
    except (OSError, ValueError):
        pass
left = piped()
print("still running:", left if left else "nothing")
