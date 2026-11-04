"""Local helper for the task board in #bot-workshop (lib/bot/cabinet_board.py). Prints a script that runs on the
VM, so pipe it there:

  python3 board.py init | run.sh -                                    post the board (first time, or if it's lost)
  python3 board.py show | run.sh -                                    print the board's rows as JSON (the ledger)
  python3 board.py add <request_id> <asker_id> "<what>" [status] [--rule oggers|mods|asker] [--note ..] [--link <id>]
  python3 board.py set <request_id> <status|-> [--note ..] [--link <id>] [--rule ..] [--what ..]
  python3 board.py remove <request_id>                                take a row off without marking it done
  python3 board.py redraw | run.sh -                                  redraw it in place

status is waiting, received, blocked, working (add's default) or done. --rule says who may press Approve on a
waiting row, following SKILL.md's go rules: oggers (the default: code, data, money), mods (Oggers or Hadidas:
permissions, roles, moderation) or asker (easy changes). --link is the message id of the plan or result card the
row points to. --note is one short line under the row ("" clears it).

Every change reposts the board at the bottom of #bot-workshop and deletes the old message, so it stays in view
(Oggers asked for that over pinning). Add --edit to change it in place instead, for a tweak nobody needs to see
jump. All changes run under the board's file lock, the same one the bot takes when someone presses a button."""
import base64, json, sys

OPS = {"init", "show", "add", "set", "remove", "redraw"}
FLAGS = {"--rule": "rule", "--note": "note", "--link": "link", "--what": "what"}


def parse(argv):
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in OPS:
        sys.exit(__doc__)
    op, rest = argv[0], argv[1:]
    args, pos, edit = {}, [], False
    while rest:
        a = rest.pop(0)
        if a == "--edit":
            edit = True
        elif a in FLAGS:
            if not rest:
                sys.exit(f"{a} needs a value")
            args[FLAGS[a]] = rest.pop(0)
        else:
            pos.append(a)
    if op == "add":
        if len(pos) not in (3, 4):
            sys.exit("add <request_id> <asker_id> \"<what>\" [status]")
        args.update(id=pos[0], asker=pos[1], what=pos[2])
        if len(pos) == 4:
            args["status"] = pos[3]
    elif op == "set":
        if len(pos) != 2:
            sys.exit("set <request_id> <status|-> [--note ..] [--link ..] [--rule ..] [--what ..]")
        args["id"] = pos[0]
        if pos[1] != "-":
            args["status"] = pos[1]
    elif op == "remove":
        if len(pos) != 1:
            sys.exit("remove <request_id>")
        args["id"] = pos[0]
    elif pos:
        sys.exit(f"{op} takes no arguments")
    for key in ("id", "asker", "link"):
        if key in args and not str(args[key]).isdigit():
            sys.exit(f"{key} must be a Discord id")
    if op in ("redraw",):
        edit = True
    return op, args, edit


def script(op, args, edit):
    blob = base64.b64encode(json.dumps({"op": op, "args": args, "edit": edit}).encode()).decode()
    return f'''import base64, json, os, sys, urllib.error, urllib.request
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv(".env")
from lib.bot import cabinet_board as cb
req = json.loads(base64.b64decode("{blob}"))
op, args, edit = req["op"], req["args"], req["edit"]
H = {{"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)",
     "Content-Type": "application/json"}}


def call(method, path, body=None):
    r = urllib.request.Request("https://discord.com/api/v10" + path, method=method, headers=H,
                               data=json.dumps(body).encode() if body is not None else None)
    raw = urllib.request.urlopen(r, timeout=20).read()
    return json.loads(raw) if raw else None


if op == "show":
    s = cb.load_state()
    print(json.dumps({{"message_id": s["message_id"], "tasks": s["tasks"]}}, indent=1, ensure_ascii=False))
    sys.exit(0)

with cb.locked():
    state = cb.load_state()
    try:
        task = cb.apply_op(state, op, args)
    except (KeyError, ValueError) as e:
        sys.exit(f"board: {{e}}")
    cid, old = state["channel_id"], state.get("message_id")
    body = cb.payload(state)
    new = None
    if edit and old:
        try:
            call("PATCH", f"/channels/{{cid}}/messages/{{old}}", body)
            new = old
        except urllib.error.HTTPError as e:
            if e.code != 404:
                sys.exit(f"board: edit failed {{e.code}} {{e.read().decode()[:400]}}")
    if new is None:  # repost at the bottom, then take the old one away
        try:
            new = call("POST", f"/channels/{{cid}}/messages", body)["id"]
        except urllib.error.HTTPError as e:
            sys.exit(f"board: post failed {{e.code}} {{e.read().decode()[:400]}}")
        if old and old != new:
            try:
                call("DELETE", f"/channels/{{cid}}/messages/{{old}}")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    print(f"board: could not delete the old board {{old}}: {{e.code}}", file=sys.stderr)
    state["message_id"] = new
    cb.save_state(state)
print(json.dumps({{"ok": True, "op": op, "message_id": new, "task": task}}, ensure_ascii=False))
'''


if __name__ == "__main__":
    print(script(*parse(sys.argv[1:])))
