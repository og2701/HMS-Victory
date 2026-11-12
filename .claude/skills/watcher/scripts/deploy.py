"""Deploy main on the VM: runs update_bot.sh (pull, restart, health) and prints the tail of its output.

Run from this folder as `./run.sh deploy.py`. It runs inside ~/HMS-Victory on the VM. This is the one
command the permission rule in .claude/settings.json allows for deploys, so keep it to exactly that.
"""
import subprocess

r = subprocess.run(["bash", "update_bot.sh"], capture_output=True, text=True, timeout=600)
print(r.stdout[-2500:])
print(r.stderr[-1500:])
print("exit", r.returncode)
