"""The watcher notes on the VM: what a fresh session should know that isn't in the skill, the code or Discord.

Lives at ~/cabinet_watch/notes.md on the VM, outside the repo checkout, so deploys and git never touch it. Prints a
script that runs on the VM, so pipe it there:

  python3 notes.py show | run.sh -                 print the notes (creating the file if it isn't there yet)
  python3 notes.py add "<Section>" "<note>" | run.sh -   add a dated bullet under that section (made if new)
  python3 notes.py put <local_file> <name> | run.sh -    save a longer file next to the notes (~/cabinet_watch/<name>)

Keep notes short and durable: decisions Oggers made, preferences, things that bit you, where long-lived files are.
Not a log of every request; #bot-ideas tickets and the task board cover that."""
import sys

args = sys.argv[1:]
if not args or args[0] not in ("show", "add", "put"):
    sys.exit(__doc__)
op = args[0]
if op == "add":
    assert len(args) == 3, 'add takes "<Section>" "<note>"'
    payload = {"section": args[1].strip(), "note": " ".join(args[2].split())}
elif op == "put":
    assert len(args) == 3, "put takes <local_file> <name>"
    assert "/" not in args[2] and not args[2].startswith("."), "name is a plain file name"
    payload = {"name": args[2], "text": open(args[1]).read()}
else:
    payload = {}

print(f"""
import datetime, fcntl, os
OP, P = {op!r}, {payload!r}
D = os.path.expanduser("~/cabinet_watch")
F = os.path.join(D, "notes.md")
os.makedirs(D, exist_ok=True)
HEAD = "# watcher notes\\n\\nWhat a fresh session should know that isn't in SKILL.md, the code or Discord. Read at start, add with notes.py.\\n"
with open(os.path.join(D, ".lock"), "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    if not os.path.exists(F):
        open(F, "w").write(HEAD)
    if OP == "add":
        lines = open(F).read().rstrip("\\n").split("\\n")
        head = "## " + P["section"]
        bullet = "- " + P["note"] + " (" + datetime.date.today().isoformat() + ")"
        if head in lines:
            i = lines.index(head) + 1
            while i < len(lines) and not lines[i].startswith("## "):
                i += 1
            while i > 0 and lines[i - 1] == "":
                i -= 1
            lines.insert(i, bullet)
        else:
            lines += ["", head, bullet]
        open(F, "w").write("\\n".join(lines) + "\\n")
        print("added under", head)
    elif OP == "put":
        path = os.path.join(D, P["name"])
        open(path, "w").write(P["text"])
        print("saved", path)
    else:
        print(open(F).read())
        extra = sorted(f for f in os.listdir(D) if f not in ("notes.md", ".lock"))
        if extra:
            print("Other files in ~/cabinet_watch:", ", ".join(extra))
""")
