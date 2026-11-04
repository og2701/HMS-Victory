"""Runs on the VM (piped over ssh). argv: the message id to start after, or "latest" for the newest one, then
optionally "pending" while a confirmation card waits for a go: any human message in that channel wakes it, since
"sounds good, build it" rarely pings Vic. Plain "pending" means #cabinet; "pending:workshop" or
"pending:cabinet,workshop" name the channels.

Polls #cabinet and #bot-workshop every few seconds and exits when anyone (not a bot) pings Vic, replies to Vic or
says "vic". Each such message gets Vic's ACK reaction and its own typing loop straight away (a card reply or a
react.py reaction to that message takes both off). It then waits a few seconds so a follow-up line from the same
person lands, and prints the channel, the hits, the 30 messages before them, everything posted after them and the
last id seen. Gives up after 6 hours with "timeout": true.

Only one watcher runs at a time: starting one stops any that's still going (to switch into pending mode, say)."""
import atexit, json, os, re, signal, subprocess, sys, time, urllib.parse, urllib.request
from dotenv import load_dotenv

load_dotenv(".env")
CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640", "dm": "1231209765754110002"}  # dm: Vic and Oggers
NAMES = {cid: name for name, cid in CHANNELS.items()}
VIC = "1171842947440967770"
ACK = "hmmnote:1179638366275383297"  # "looking into it", added the moment a request is seen
POLL_SECONDS = 5
FOLLOW_UP_SECONDS = 6  # Vic's own chat responder stays out of these channels, so this only waits for a second line
start = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else ""
if "dm" not in sys.argv[2:]:  # Oggers' DM is only watched when asked for: watch.py <id> [pending...] dm
    WATCHED = {k: v for k, v in CHANNELS.items() if k != "dm"}
else:
    WATCHED = CHANNELS
    if mode == "dm":
        mode = ""
pending = ({CHANNELS[n] for n in (mode.split(":", 1)[1].split(",") if ":" in mode else ["cabinet"])}
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


def mentions_vic(m):
    return any(u["id"] == VIC for u in m.get("mentions", []))


def addresses_vic(m):
    if m.get("channel_id") == DM:  # everything in the DM is aimed at Vic
        return True
    ref = (m.get("referenced_message") or {}).get("author", {}).get("id")
    return bool(ref == VIC or any(u["id"] == VIC for u in m.get("mentions", []))
                or re.search(r"\bvic\b", text_of(m), re.I))


def wanted(m, cid):
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
    cid = CHANNELS[h["channel"]]
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{cid}/messages/{h['id']}/reactions/{urllib.parse.quote(ACK)}/@me",
        data=b"", method="PUT", headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("ack failed", h["id"], e, file=sys.stderr)


def start_typing(h):
    """One typing loop per request (keyed by its message id), so parallel jobs end only their own."""
    pid_file = f"/tmp/vic_typing/{h['id']}.pid"
    try:
        os.kill(int(open(pid_file).read()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    os.makedirs("/tmp/vic_typing", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-c", TYPING_LOOP, CHANNELS[h["channel"]]], start_new_session=True,
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
    for m in sorted(get(f"/channels/{cid}/messages?after={after[cid]}&limit=50"), key=lambda m: int(m["id"])):
        after[cid] = m["id"]
        if wanted(m, cid):
            hits.append(dict(brief(m), addresses_vic=addresses_vic(m), channel=NAMES[cid]))
    return hits


while time.time() < deadline:
    try:
        hits = [h for cid in WATCHED.values() for h in poll(cid)]
    except Exception as e:  # network blips just retry
        print("poll error", e, file=sys.stderr)
        time.sleep(30)
        continue
    if hits:
        cid = CHANNELS[hits[0]["channel"]]
        aimed = [h for h in hits if h["addresses_vic"]]
        for h in aimed:  # plain chatter in pending mode gets neither the ACK nor typing
            ack(h)
            start_typing(h)
        time.sleep(FOLLOW_UP_SECONDS)
        before = sorted(get(f"/channels/{cid}/messages?before={hits[0]['id']}&limit=30"), key=lambda m: int(m["id"]))
        later = sorted(get(f"/channels/{cid}/messages?after={hits[0]['id']}&limit=50"), key=lambda m: int(m["id"]))
        if later:
            after[cid] = max(after[cid], later[-1]["id"], key=int)
        for m in later:  # a second request in the same channel during the wait gets its ACK too
            if wanted(m, cid) and addresses_vic(m) and m["id"] not in {h["id"] for h in hits}:
                h = dict(brief(m), addresses_vic=True, channel=NAMES[cid])
                ack(h)
                start_typing(h)
                hits.append(h)
        for other in WATCHED.values():  # anything aimed at Vic elsewhere during the wait comes along too
            if other != cid:
                extra = poll(other)
                for h in extra:
                    if h["addresses_vic"]:
                        ack(h)
                        start_typing(h)
                hits += extra
        print(json.dumps({"channel": NAMES[cid], "channel_id": cid, "hits": hits,
                          "context_before": [brief(m) for m in before],
                          "after_first_hit": [brief(m) for m in later],
                          "last_id": max(after.values(), key=int)}))
        sys.exit(0)
    time.sleep(POLL_SECONDS)
print(json.dumps({"hits": [], "last_id": max(after.values(), key=int), "timeout": True}))
