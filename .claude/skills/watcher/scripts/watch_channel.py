"""Runs on the VM (piped over ssh). Starts, ends and lists the watcher's watches on channels other than #bot-workshop.

  watch_channel.py add <channel_id> [duration] [user_id ...] --request <request_id>
  watch_channel.py end <channel_id|all> [--by <user_id>]
  watch_channel.py list

duration is minutes ("45"), or "90m", "2h", "1h30m"; 30 minutes by default, 24 hours at most. Oggers and Hadidas are
always on a watch; name anyone else Oggers lets in. Adding a channel that's already watched updates it: the new
duration counts from now and the users are merged.

While a watch is on, watch.py polls that channel too and wakes on the listed users' pings, replies to Vic and "vic"
there (nobody else's), and Vic's own chat replies stand down for those users in that channel
(lib/features/watcher_channels.py). Both go by the expiry time, and the bot also wants the watcher's heartbeat from the
last 5 minutes, so a crashed session can't leave a channel paused. watch.py reports an expiry as a "watch_expired" hit.

State: data/watcher/extra_channels.json. Every change is appended to data/watcher/log.jsonl. Prints JSON."""
import fcntl, json, os, re, subprocess, sys, time, urllib.error, urllib.request
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv(".env")
DIR = os.path.join("data", "watcher")
STATE = os.path.join(DIR, "extra_channels.json")
LOCK = os.path.join(DIR, ".lock")
LOG = os.path.join(DIR, "log.jsonl")
GUILD = "959493056242008184"
WORKSHOP = "1141037835445616640"
OGGERS, HADIDAS = "404634271861571584", "198144909583056898"
DEFAULT_MINUTES, MAX_MINUTES, MAX_WATCHES = 30, 24 * 60, 3
MESSAGE_CHANNEL_TYPES = {0, 2, 5, 10, 11, 12}  # text, voice chat, announcement, threads


def get(path):
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


@contextmanager
def locked():
    os.makedirs(DIR, exist_ok=True)
    with open(LOCK, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load():
    try:
        return json.load(open(STATE)).get("watches", [])
    except (OSError, ValueError, AttributeError):
        return []


def save(watches):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"watches": watches}, f, indent=1)
    os.replace(tmp, STATE)


def log(event, **fields):
    with open(LOG, "a") as f:
        f.write(json.dumps({"at": int(time.time()), "event": event, **fields}) + "\n")


def minutes_of(text):
    if text.isdigit():
        return int(text)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", text.lower())
    if not m or not any(m.groups()):
        raise ValueError(text)
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def describe(w, now):
    return {**w, "minutes_left": max(0, round((w["expires_at"] - now) / 60))}


def add(args):
    if "--request" not in args:
        sys.exit("add needs --request <request_id>")
    i = args.index("--request")
    request_id = args[i + 1]
    args = args[:i] + args[i + 2:]
    channel_id, rest = args[0], args[1:]
    minutes = DEFAULT_MINUTES
    if rest and not (rest[0].isdigit() and len(rest[0]) >= 15):  # a user id is a long number, a duration isn't
        minutes = minutes_of(rest.pop(0))
    if not 1 <= minutes <= MAX_MINUTES:
        sys.exit(f"duration must be 1 to {MAX_MINUTES} minutes")
    users = [u.strip("<@!>") for u in rest]
    if not all(u.isdigit() for u in users):
        sys.exit(f"bad user id in {rest}")
    if channel_id == WORKSHOP:
        sys.exit("#bot-workshop is always watched")
    try:
        ch = get(f"/channels/{channel_id}")
    except urllib.error.HTTPError as e:
        sys.exit(f"can't see channel {channel_id}: {e.code}")
    if ch.get("guild_id") != GUILD or ch.get("type") not in MESSAGE_CHANNEL_TYPES:
        sys.exit(f"channel {channel_id} isn't a text channel or thread in the server")
    page = get(f"/channels/{channel_id}/messages?limit=1")
    newest = page[0]["id"] if page else "0"
    now = time.time()
    warnings = []
    if subprocess.run(["systemctl", "is-active", "--quiet", "persona-bot"]).returncode == 0:
        warnings.append("the persona bot is running: if it's on in this server it still answers tags here")
    with locked():
        watches = [w for w in load() if w.get("expires_at", 0) > now]
        old = next((w for w in watches if w["channel_id"] == channel_id), None)
        if not old and len(watches) >= MAX_WATCHES:
            sys.exit(f"already {MAX_WATCHES} watches on; end one first")
        merged = list(dict.fromkeys([OGGERS, HADIDAS] + (old["users"] if old else []) + users))
        w = {"channel_id": channel_id, "name": ch.get("name"), "users": merged, "expires_at": int(now + minutes * 60),
             "request_id": request_id, "started_at": old["started_at"] if old else int(now),
             "after": old["after"] if old else newest}
        watches = [x for x in watches if x["channel_id"] != channel_id] + [w]
        save(watches)
        log("update" if old else "add", **w)
    print(json.dumps({"ok": True, "updated" if old else "added": describe(w, now), "warnings": warnings}))


def end(args):
    target = args[0]
    by = args[args.index("--by") + 1] if "--by" in args else None
    with locked():
        watches = load()
        gone = [w for w in watches if target in ("all", w["channel_id"])]
        save([w for w in watches if w not in gone])
        for w in gone:
            log("end", channel_id=w["channel_id"], name=w.get("name"), request_id=w.get("request_id"), by=by)
    print(json.dumps({"ok": True, "ended": [w["channel_id"] for w in gone]}))


def show():
    now = time.time()
    print(json.dumps({"watches": [describe(w, now) for w in load() if w.get("expires_at", 0) > now]}, indent=1))


cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("list", [])
{"add": add, "end": end, "list": lambda a: show()}[cmd](args)
