"""Runs on the VM (piped over ssh). argv: the message id to start after, or "latest" for the newest one, then
optionally "pending" (or "pending:workshop") while a confirmation card waits for a go in that channel: any human
message there wakes it, since "sounds good, build it" rarely pings Vic. Plain "pending" means #cabinet.

Polls #cabinet and #bot-workshop every few seconds and exits when anyone (not a bot) pings Vic, replies to Vic or
says "vic". Each such message gets Vic's ACK reaction and Vic starts typing in that channel straight away (a card
reply or a reaction from react.py takes the ACK off; typing_indicator.py stop or the next card post ends the
typing). It then waits a few seconds so a follow-up line from the same person lands, and prints the channel, the
hits, the 30 messages before them, everything posted after them and the last id seen. Gives up after 6 hours with
"timeout": true."""
import json, os, re, signal, subprocess, sys, time, urllib.parse, urllib.request
from dotenv import load_dotenv

load_dotenv(".env")
CHANNELS = {"cabinet": "1155637791917613147", "workshop": "1141037835445616640"}
NAMES = {cid: name for name, cid in CHANNELS.items()}
VIC = "1171842947440967770"
ACK = "hmmnote:1179638366275383297"  # "looking into it", added the moment a request is seen
POLL_SECONDS = 5
FOLLOW_UP_SECONDS = 6  # Vic's own chat responder stays out of these channels, so this only waits for a second line
start = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else ""
pending = CHANNELS[mode.split(":", 1)[1] if ":" in mode else "cabinet"] if mode.startswith("pending") else None
deadline = time.time() + 6 * 3600


def get(path):
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": "Bot " + os.environ["DISCORD_TOKEN"], "User-Agent": "DiscordBot (x, 1)"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def newest(cid):
    page = get(f"/channels/{cid}/messages?limit=1")
    return page[0]["id"] if page else "0"


# Each channel keeps its own place, so a slow poll in one can't skip messages in the other.
after = {cid: (newest(cid) if start == "latest" else start) for cid in CHANNELS.values()}


def addresses_vic(m):
    ref = (m.get("referenced_message") or {}).get("author", {}).get("id")
    return bool(ref == VIC or any(u["id"] == VIC for u in m.get("mentions", []))
                or re.search(r"\bvic\b", text_of(m), re.I))


def wanted(m, cid):
    return not m["author"].get("bot") and (cid == pending or addresses_vic(m))


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


def start_typing(cid):
    try:
        os.kill(int(open("/tmp/vic_typing.pid").read()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    p = subprocess.Popen([sys.executable, "-c", TYPING_LOOP, cid], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open("/tmp/vic_typing.pid", "w").write(str(p.pid))


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
        hits = [h for cid in CHANNELS.values() for h in poll(cid)]
    except Exception as e:  # network blips just retry
        print("poll error", e, file=sys.stderr)
        time.sleep(30)
        continue
    if hits:
        cid = CHANNELS[hits[0]["channel"]]
        aimed = [h for h in hits if h["addresses_vic"]]
        if aimed:  # plain chatter in pending mode gets neither the ACK nor typing
            for h in aimed:
                ack(h)
            start_typing(CHANNELS[aimed[0]["channel"]])
        time.sleep(FOLLOW_UP_SECONDS)
        before = sorted(get(f"/channels/{cid}/messages?before={hits[0]['id']}&limit=30"), key=lambda m: int(m["id"]))
        later = sorted(get(f"/channels/{cid}/messages?after={hits[0]['id']}&limit=50"), key=lambda m: int(m["id"]))
        if later:
            after[cid] = max(after[cid], later[-1]["id"], key=int)
        for m in later:  # a second request in the same channel during the wait gets its ACK too
            if wanted(m, cid) and addresses_vic(m) and m["id"] not in {h["id"] for h in hits}:
                h = dict(brief(m), addresses_vic=True, channel=NAMES[cid])
                ack(h)
                hits.append(h)
        for other in CHANNELS.values():  # anything aimed at Vic elsewhere during the wait comes along too
            if other != cid:
                extra = poll(other)
                for h in extra:
                    if h["addresses_vic"]:
                        ack(h)
                hits += extra
        print(json.dumps({"channel": NAMES[cid], "channel_id": cid, "hits": hits,
                          "context_before": [brief(m) for m in before],
                          "after_first_hit": [brief(m) for m in later],
                          "last_id": max(after.values(), key=int)}))
        sys.exit(0)
    time.sleep(POLL_SECONDS)
print(json.dumps({"hits": [], "last_id": max(after.values(), key=int), "timeout": True}))
