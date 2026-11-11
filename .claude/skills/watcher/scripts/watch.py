"""Runs on the VM (piped over ssh). argv: the message id to start after, or "latest" for the newest one, then
optionally "pending" while a confirmation card waits for a go: any human message in that channel wakes it, since
"sounds good, build it" rarely pings Vic. Plain "pending" means #bot-workshop; "pending:dm" or
"pending:workshop,dm" name the channels. Add "dm" to watch Oggers' DM too, or "cabinet" to watch #cabinet again
(it stopped being watched on 4 Nov 2026, at Oggers' ask).

Polls #bot-workshop every few seconds and exits when anyone (not a bot) pings Vic, replies to Vic or
says "vic". Each such message gets Vic's ACK reaction and its own typing loop straight away (a card reply or a
react.py reaction to that message takes both off). It then waits a few seconds so a follow-up line from the same
person lands, and prints the channel, the hits, the 30 messages before them, everything posted after them and the
last id seen. Gives up after 6 hours with "timeout": true.

Edits count too: every poll also looks at the last EDIT_WINDOW messages in each channel, and a message that would
have been a hit and has been edited since the watcher last saw it comes through again with "edited": true, so the
new wording gets re-read (an @Vic added in an edit counts as well). What it has seen is kept in EDITS_FILE, so an
edit made while no watcher was running (between a hit and the restart) still turns up on the next run.

It also wakes when someone presses a button on the task board in #bot-workshop (lib/bot/cabinet_board.py): the
bot writes each press to data/cabinet_board/events/, and each one comes through as a hit with "type": "board",
the action (approve, reject or reply), the task (its request id, number and what), who pressed (user_id) and, for
a reply, their text in "content". The bot has already checked they may press and answered them privately. An
approve or reply starts that request's typing loop, like a ping does.

It also covers any channels Oggers has asked Vic to watch for a while (watch_channel.py keeps them in
data/watcher/extra_channels.json, re-read every poll, so no restart is needed). There only the watch's listed users'
pings, replies to Vic and "vic" count, pending mode doesn't apply, and those hits carry the channel's id as "channel"
(the card, react, history and typing scripts take an id too). While a watch is on it touches data/watcher/heartbeat
every poll; the bot only stands its own replies down while that's fresh. When a watch runs out it's taken out of the
file and comes through as a hit with "type": "watch_expired". With no file, none of this does anything.

Only one watcher runs at a time: starting one stops any that's still going (to switch into pending mode, say)."""
import atexit, fcntl, json, os, re, signal, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from dotenv import load_dotenv

load_dotenv(".env")
CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640", "dm": "1231209765754110002"}  # dm: Vic and Oggers
NAMES = {cid: name for name, cid in CHANNELS.items()}
VIC = "1171842947440967770"
ACK = "hmmnote:1179638366275383297"  # "looking into it", added the moment a request is seen
POLL_SECONDS = 5
FOLLOW_UP_SECONDS = 6  # Vic's own chat responder stays out of these channels, so this only waits for a second line
BOARD_EVENTS = os.path.join("data", "cabinet_board", "events")
BOARD_SEEN = os.path.join(BOARD_EVENTS, "seen")
EXTRA_DIR = os.path.join("data", "watcher")
EXTRA_FILE = os.path.join(EXTRA_DIR, "extra_channels.json")
EXTRA_LOCK = os.path.join(EXTRA_DIR, ".lock")
EXTRA_LOG = os.path.join(EXTRA_DIR, "log.jsonl")
HEARTBEAT = os.path.join(EXTRA_DIR, "heartbeat")
EDITS_FILE = "/tmp/vic_watch_edits.json"  # channel id -> {message id: its edited_timestamp when last seen}
EDIT_WINDOW = 25
start = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else ""
# #bot-workshop always; Oggers' DM and #cabinet only when asked for: watch.py <id> [pending...] [dm] [cabinet]
WATCHED = {k: v for k, v in CHANNELS.items() if k == "workshop" or k in sys.argv[2:]}
if mode in ("dm", "cabinet"):
    mode = ""
pending = ({CHANNELS[n] for n in (mode.split(":", 1)[1].split(",") if ":" in mode else ["workshop"])}
           if mode.startswith("pending") else set())
deadline = time.time() + 6 * 3600

WATCH_PID = "/tmp/vic_watch.pid"
try:
    old = int(open(WATCH_PID).read())
    # Only if it's still a piped python: a stale file could name a pid that's since been reused by something else.
    if old != os.getpid() and open(f"/proc/{old}/cmdline", "rb").read().split(b"\0")[:2] == [b"venv/bin/python", b"-"]:
        os.kill(old, signal.SIGTERM)
except (OSError, ValueError):
    pass
open(WATCH_PID, "w").write(str(os.getpid()))


@atexit.register
def _clear_pid():  # so a later script that happens to get this pid is never taken for the watcher
    try:
        if open(WATCH_PID).read() == str(os.getpid()):
            os.remove(WATCH_PID)
    except OSError:
        pass


def get(path):
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def newest(cid):
    page = get(f"/channels/{cid}/messages?limit=1")
    return page[0]["id"] if page else "0"


# Each channel keeps its own place, so a slow poll in one can't skip messages in the other.
after = {cid: (newest(cid) if start == "latest" else start) for cid in WATCHED.values()}


DM = CHANNELS["dm"]
extra = {}  # channel id -> its watch, for the other channels Oggers asked Vic to watch


def channel_id(name):
    return CHANNELS.get(name, name)


def channel_name(cid):
    return NAMES.get(cid, cid)


def refresh_extra():
    """Re-read the watches. Returns the ones that have just run out (taken out of the file and logged)."""
    global extra
    if not os.path.exists(EXTRA_FILE):
        if extra:
            for cid in extra:
                after.pop(cid, None)
            extra = {}
        return []
    now = time.time()
    with open(EXTRA_LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                watches = [w for w in json.load(open(EXTRA_FILE)).get("watches", []) if isinstance(w, dict)]
            except (OSError, ValueError, AttributeError) as e:
                print("bad extra_channels.json", e, file=sys.stderr)
                watches = []
            expired = [w for w in watches if w.get("expires_at", 0) <= now]
            if expired:
                tmp = EXTRA_FILE + ".tmp"
                json.dump({"watches": [w for w in watches if w not in expired]}, open(tmp, "w"), indent=1)
                os.replace(tmp, EXTRA_FILE)
                with open(EXTRA_LOG, "a") as f:
                    for w in expired:
                        f.write(json.dumps({"at": int(now), "event": "expired", "channel_id": w.get("channel_id"),
                                            "name": w.get("name"), "request_id": w.get("request_id")}) + "\n")
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    live = {str(w["channel_id"]): w for w in watches if w not in expired and str(w.get("channel_id")) not in WATCHED.values()}
    for cid in set(extra) - set(live):
        after.pop(cid, None)
    for cid, w in live.items():
        if cid not in after:  # from where the watch began, or where this watcher was told to start if that's later
            after[cid] = newest(cid) if start == "latest" else max(str(w.get("after") or start), start, key=int)
    extra = live
    if extra:
        open(HEARTBEAT, "a").close()
        os.utime(HEARTBEAT)
    return [{"type": "watch_expired", "channel": "workshop", "id": w.get("request_id"), "channel_id": w.get("channel_id"),
             "name": w.get("name"), "users": w.get("users"), "addresses_vic": False, "bot": False} for w in expired]


def polled_channels():
    return list(WATCHED.values()) + [cid for cid in extra if cid not in WATCHED.values()]


def mentions_vic(m):
    return any(u["id"] == VIC for u in m.get("mentions", []))


def addresses_vic(m):
    if m.get("channel_id") == DM:  # everything in the DM is aimed at Vic
        return True
    ref = (m.get("referenced_message") or {}).get("author", {}).get("id")
    return bool(ref == VIC or any(u["id"] == VIC for u in m.get("mentions", []))
                or re.search(r"\bvic\b", text_of(m), re.I))


def wanted(m, cid):
    if cid in extra:  # a channel Oggers asked Vic to watch: only the watch's users, and only when aimed at Vic
        return not m["author"].get("bot") and m["author"]["id"] in {str(u) for u in extra[cid].get("users", [])} \
            and addresses_vic(m)
    if cid == DM:  # Vic's own responder answers Oggers' @mentions in DMs, so only plain messages come here
        return not m["author"].get("bot") and not mentions_vic(m)
    return not m["author"].get("bot") and (cid in pending or addresses_vic(m))


TYPING_LOOP = r'''
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


def ack(h):
    """React with ACK so the asker knows it's been seen, long before the answer is ready."""
    cid = channel_id(h["channel"])
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{cid}/messages/{h['id']}/reactions/{urllib.parse.quote(ACK)}/@me",
        data=b"", method="PUT", headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("ack failed", h["id"], e, file=sys.stderr)


BATCH_LOOPS = {}  # channel id -> the request id whose loop this batch's other hits in that channel share


def start_typing(h):
    """One typing loop per channel per batch, keyed by the first request's id. Later hits in the same channel get a
    <id>.alias file pointing at it, so answering ANY of them (card.py reply, react.py, typing_indicator.py stop <id>)
    ends the loop. Discord shows one "Vic is typing" per channel anyway, and a hit left unanswered in a burst can't
    orphan a loop. Separate batches (parallel jobs) still keep separate loops."""
    cid = channel_id(h["channel"])
    if cid in BATCH_LOOPS and BATCH_LOOPS[cid] != h["id"]:
        os.makedirs("/tmp/vic_typing", exist_ok=True)
        open(f"/tmp/vic_typing/{h['id']}.alias", "w").write(BATCH_LOOPS[cid])
        return
    BATCH_LOOPS[cid] = h["id"]
    pid_file = f"/tmp/vic_typing/{h['id']}.pid"
    try:
        os.kill(int(open(pid_file).read()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    os.makedirs("/tmp/vic_typing", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-c", TYPING_LOOP, channel_id(h["channel"])], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open(pid_file, "w").write(str(p.pid))


def text_of(m):
    """Message text, including the text inside Components V2 cards (Vic's replies)."""
    parts = [m["content"]] if m.get("content") else []
    stack = list(m.get("components") or [])
    while stack:
        c = stack.pop(0)
        if c.get("type") == 10:
            parts.append(c.get("content", ""))
        stack[:0] = c.get("components") or []
    return "\n".join(parts)


def brief(m):
    return {"id": m["id"], "author": m["author"].get("global_name") or m["author"]["username"], "bot": bool(m["author"].get("bot")),
            "content": text_of(m), "attachments": [x["url"] for x in m["attachments"]],
            "reply_to": (m.get("referenced_message") or {}).get("id")}


def poll(cid):
    """New hits in one channel since its last poll."""
    hits = []
    try:
        page = get(f"/channels/{cid}/messages?after={after[cid]}&limit=50")
    except urllib.error.HTTPError as e:
        if cid not in extra:
            raise
        print("can't read watched channel", cid, e.code, file=sys.stderr)  # a bad watch mustn't stall the rest
        return []
    for m in sorted(page, key=lambda m: int(m["id"])):
        after[cid] = m["id"]
        if wanted(m, cid):
            hits.append(dict(brief(m), addresses_vic=addresses_vic(m), channel=channel_name(cid)))
    return hits


try:
    edits = json.load(open(EDITS_FILE))
except (OSError, ValueError):
    edits = {}


def check_edits(cid):
    """Messages already past this channel's place that have been edited since they were last seen. Anything seen
    for the first time just becomes the baseline, so old edits don't fire."""
    try:
        page = get(f"/channels/{cid}/messages?limit={EDIT_WINDOW}")
    except urllib.error.HTTPError as e:
        if cid not in extra:
            raise
        print("can't read watched channel", cid, e.code, file=sys.stderr)
        return []
    known = edits.setdefault(cid, {})
    hits = []
    for m in sorted(page, key=lambda m: int(m["id"])):
        ts = m.get("edited_timestamp")
        first = m["id"] not in known
        prev = known.get(m["id"])
        known[m["id"]] = ts
        if not first and ts and ts != prev and int(m["id"]) <= int(after[cid]) and wanted(m, cid):
            hits.append(dict(brief(m), addresses_vic=addresses_vic(m), channel=channel_name(cid), edited=True))
    for mid in sorted(known, key=int)[:-4 * EDIT_WINDOW]:  # keep a few windows' worth
        known.pop(mid, None)
    try:
        tmp = EDITS_FILE + ".tmp"
        json.dump(edits, open(tmp, "w"))
        os.replace(tmp, EDITS_FILE)
    except OSError as e:
        print("couldn't save edits", e, file=sys.stderr)
    return hits


def board_hits():
    """Task board presses the bot has recorded since the last look. Each file is moved to seen/ as it's read, so a
    press is handed over once; seen/ keeps a day of them for reference."""
    try:
        names = sorted(n for n in os.listdir(BOARD_EVENTS) if n.endswith(".json"))
    except FileNotFoundError:
        return []
    os.makedirs(BOARD_SEEN, mode=0o700, exist_ok=True)
    hits = []
    for name in names:
        path = os.path.join(BOARD_EVENTS, name)
        try:
            ev = json.load(open(path))
            os.replace(path, os.path.join(BOARD_SEEN, name))
        except (OSError, ValueError) as e:
            print("bad board event", name, e, file=sys.stderr)
            try:
                os.replace(path, os.path.join(BOARD_SEEN, name + ".bad"))
            except OSError:
                pass
            continue
        hits.append({"type": "board", "channel": "workshop", "id": ev["task"], "action": ev["action"],
                     "no": ev.get("no"), "what": ev.get("what"), "asker": ev.get("asker"),
                     "user_id": ev["user_id"], "author": f"<@{ev['user_id']}>", "content": ev.get("text", ""),
                     "addresses_vic": False, "bot": False})
    cutoff = time.time() - 86400
    for name in os.listdir(BOARD_SEEN):
        try:
            if os.path.getmtime(os.path.join(BOARD_SEEN, name)) < cutoff:
                os.remove(os.path.join(BOARD_SEEN, name))
        except OSError:
            pass
    return hits


def board_typing(board):
    for h in board:
        if h.get("action") in ("approve", "reply"):
            start_typing(h)


while time.time() < deadline:
    try:
        expired = refresh_extra()
        hits = [h for cid in polled_channels() for h in poll(cid)]
        hits += [h for cid in polled_channels() for h in check_edits(cid)]  # after the polls, so new ones aren't "edits"
        board = board_hits()  # after the polls, so a network blip can't drop a press it has already taken
    except Exception as e:  # network blips just retry
        print("poll error", e, file=sys.stderr)
        time.sleep(30)
        continue
    board = expired + board  # a run-out watch travels with the presses: no message context needed
    if board and not hits:  # only presses: hand them over now, no message context needed
        board_typing(board)
        print(json.dumps({"channel": "workshop", "channel_id": CHANNELS["workshop"], "hits": board,
                          "context_before": [], "after_first_hit": [], "last_id": max(after.values(), key=int)}))
        sys.exit(0)
    if hits:
        cid = channel_id(hits[0]["channel"])
        aimed = [h for h in hits if h["addresses_vic"]]
        for h in aimed:  # plain chatter in pending mode gets neither the ACK nor typing
            ack(h)
            if not h.get("edited"):  # an edit usually needs no answer, so it gets the ACK but no typing to orphan
                start_typing(h)
        time.sleep(FOLLOW_UP_SECONDS)
        before = sorted(get(f"/channels/{cid}/messages?before={hits[0]['id']}&limit=30"), key=lambda m: int(m["id"]))
        later = sorted(get(f"/channels/{cid}/messages?after={hits[0]['id']}&limit=50"), key=lambda m: int(m["id"]))
        if later:
            after[cid] = max(after[cid], later[-1]["id"], key=int)
        for m in later:  # a second request in the same channel during the wait gets its ACK too
            if wanted(m, cid) and addresses_vic(m) and m["id"] not in {h["id"] for h in hits}:
                h = dict(brief(m), addresses_vic=True, channel=channel_name(cid))
                ack(h)
                start_typing(h)
                hits.append(h)
        for other in polled_channels():  # anything aimed at Vic elsewhere during the wait comes along too
            if other != cid:
                more = poll(other)  # (not "extra": that's the watched-channels dict)
                for h in more:
                    if h["addresses_vic"]:
                        ack(h)
                        start_typing(h)
                hits += more
        board += board_hits()  # presses come along after the messages
        board_typing(board)
        hits += board
        print(json.dumps({"channel": channel_name(cid), "channel_id": cid, "hits": hits,
                          "context_before": [brief(m) for m in before],
                          "after_first_hit": [brief(m) for m in later],
                          "last_id": max(after.values(), key=int)}))
        sys.exit(0)
    time.sleep(POLL_SECONDS)
print(json.dumps({"hits": [], "last_id": max(after.values(), key=int), "timeout": True}))
