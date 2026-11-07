"""Local helper. Asks the running bot to do one named action (lib/bot/bot_actions.py) and prints its result.
It prints a script that runs on the VM, so pipe it there:

  python3 bot_action.py <action> ['<json args>' | @args.json] | run.sh -

The VM script drops the request into data/bot_actions/inbox/, the bot picks it up within 15 seconds (it checks
4 times a minute), and the result is printed as JSON: {"ok": true, "result": ...} or {"ok": false, "error": ...}.
It waits up to 60 seconds. If the bot never picked the request up (it's down or restarting), the request is
withdrawn so it can't run later by surprise; if the bot took it but hasn't finished, it says so and the result
lands in data/bot_actions/outbox/<id>.json. Exit code 0 only when the action succeeded.

Actions (the bot refuses anything else, and any arg not listed):
  pred_list                                      live predictions
  pred_list_scheduled                            pending scheduled predictions
  pred_schedule  channel_id title options when duration
                 when: minutes from now, "DD/MM/YYYY HH:MM" UK time (day first), or <t:unix>
                 duration: minutes after posting, or an end "DD/MM/YYYY HH:MM" UK time
  pred_create    channel_id title options duration
  pred_cancel_scheduled  sched_id
  pred_lock / pred_unlock  message_id

Everything runs as Oggers and is logged to #bot-usage-log."""
import base64, json, sys, uuid

ACTIONS = {"pred_list", "pred_list_scheduled", "pred_schedule", "pred_create", "pred_cancel_scheduled",
           "pred_lock", "pred_unlock"}
WAIT_SECONDS = 60


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    action, raw = argv[0], (argv[1] if len(argv) > 1 else "{}")
    if action not in ACTIONS:
        sys.exit(f"unknown action {action!r}; one of: {', '.join(sorted(ACTIONS))}")
    if raw.startswith("@"):
        raw = open(raw[1:]).read()
    try:
        args = json.loads(raw)
    except ValueError as e:
        sys.exit(f"args aren't valid JSON: {e}")
    if not isinstance(args, dict):
        sys.exit("args must be a JSON object")
    req_id = uuid.uuid4().hex
    payload = base64.b64encode(json.dumps({"id": req_id, "action": action, "args": args}).encode()).decode()
    print(f'''import base64, json, os, sys, time
base = os.path.join("data", "bot_actions")
inbox, outbox, processing = (os.path.join(base, d) for d in ("inbox", "outbox", "processing"))
if not os.path.isdir(inbox):
    sys.exit("no {{}} folder: the bot's action queue isn't running (deploy it, or the bot hasn't started)".format(inbox))
req = json.loads(base64.b64decode("{payload}"))
req["ts"] = time.time()
name = req["id"] + ".json"
tmp = os.path.join(inbox, "." + name + ".tmp")
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(req, f)
os.replace(tmp, os.path.join(inbox, name))
out = os.path.join(outbox, name)
deadline = time.time() + {WAIT_SECONDS}
while time.time() < deadline:
    if os.path.exists(out):
        time.sleep(0.2)
        with open(out) as f:
            res = json.load(f)
        os.remove(out)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(0 if res.get("ok") else 1)
    time.sleep(1)
try:
    os.remove(os.path.join(inbox, name))
    sys.exit("timed out after {WAIT_SECONDS}s: the bot never picked the request up, so it was withdrawn (is the bot running?)")
except FileNotFoundError:
    sys.exit("timed out after {WAIT_SECONDS}s: the bot took the request but hasn't finished; its result will be in " + out)
''')


if __name__ == "__main__":
    main(sys.argv[1:])
