"""Runs on the VM. Stops the watcher and the typing indicator. Killing the local ssh leaves the remote python running,
so this is how the watcher really ends. Only stdin-piped scripts (`python - <arg>`) match, never the bot (run.py)."""
import os, signal, subprocess

me = {os.getpid(), os.getppid()}  # this script and the ssh shell that launched it match the pattern too
for pid in subprocess.run(["pgrep", "-f", "venv/bin/python - "], capture_output=True, text=True).stdout.split():
    if int(pid) not in me:
        os.kill(int(pid), signal.SIGTERM)
        print("stopped", pid)
try:
    os.kill(int(open("/tmp/vic_typing.pid").read()), signal.SIGTERM)
    os.remove("/tmp/vic_typing.pid")
    print("stopped typing")
except (OSError, ValueError):
    pass
left = [p for p in subprocess.run(["pgrep", "-f", "venv/bin/python - "], capture_output=True, text=True).stdout.split()
        if int(p) not in me]
print("still running:", left if left else "nothing")
