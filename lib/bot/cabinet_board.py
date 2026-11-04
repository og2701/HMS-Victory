"""Vic's task board in #bot-workshop: one Components V2 message that lists every request
the watcher skill is on, with Approve / Reject / Reply buttons on the rows waiting for a go.
board.py reposts it at the bottom of the channel (and deletes the old one) whenever it
changes a row, so it stays in view; a button press edits it in place, since the presser is
looking right at it, and moves the row to ⏳ received until the session acts.

Two processes touch it, and both go through this module:

- the watcher session, through .claude/skills/watcher/scripts/board.py, whose VM
  script imports this file to add rows, change their status and redraw the message over REST;
- the live bot, which gets the button presses (interactions only ever reach the gateway
  connection) and handles them with the DynamicItems below.

State lives in data/cabinet_board/workshop.json and every change to it is made under an
exclusive flock on a sidecar lock file, so a press and a board.py update can't trample each
other. A press is written to data/cabinet_board/events/<interaction id>.json, which watch.py
picks up on its next poll and hands to the session as a `board` hit.

Who may press is decided by the rule in the button's custom_id, matching the watcher skill's go
rules (SKILL.md, "Changes"):

    o  Oggers only: code, deleting data, money moves
    m  Oggers or Hadidas: permissions, roles, moderation
    a  whoever asked (or Oggers): easy changes such as adding a role or renaming a channel

Approve needs the rule's people. Reject and Reply are also open to the person who asked, so
they can withdraw or clarify their own request. Anyone else gets a private note saying who
it's waiting on, and nothing is recorded. The custom_id is set only by Vic's own messages and
interaction.user is vouched for by Discord, so a press is as good as a typed "go".
"""

import asyncio
import contextlib
import json
import logging
import os
import time

import discord

from config import CHANNELS, DATA_DIR, GUILD_ID, USERS

logger = logging.getLogger(__name__)

BOARD_DIR = os.path.join(DATA_DIR, "cabinet_board")
STATE_FILE = os.path.join(BOARD_DIR, "workshop.json")
LOCK_FILE = os.path.join(BOARD_DIR, "workshop.lock")
EVENTS_DIR = os.path.join(BOARD_DIR, "events")
CHANNEL_ID = CHANNELS.BOT_WORKSHOP

STATUSES = ("waiting", "received", "blocked", "working", "done")
HEADINGS = {
    "waiting": "### 🟠 Waiting on a go",
    "received": "### ⏳ Received, processing",
    "blocked": "### 🔴 Blocked",
    "working": "### 🔵 Working",
    "done": "### 🟢 Done",
}
ACCENTS = {"waiting": 0xE67E22, "received": 0xE67E22, "blocked": 0xE74C3C, "working": 0x00247D, "done": 0x2ECC71}
RULES = ("o", "m", "a")
RULE_NAMES = {"oggers": "o", "mods": "m", "asker": "a"}
KEEP_DONE = 5            # done rows shown; older ones roll into history
KEEP_HISTORY = 200
MAX_BUTTON_ROWS = 5      # 🟠 rows that get buttons: each costs 5 of the 40 components
MAX_COMPONENTS = 40
MAX_TEXT = 3900          # CV2 allows 4000 characters across a message's text displays
WHAT_CHARS = 100
NOTE_CHARS = 120
EVENT_KEEP_SECONDS = 24 * 3600


# ---------------------------------------------------------------- permissions

def approvers(rule: str, asker: int) -> set:
    if rule == "m":
        return {USERS.OGGERS, USERS.HADIDAS}
    if rule == "a":
        return {int(asker), USERS.OGGERS}
    return {USERS.OGGERS}


def may_press(verb: str, rule: str, asker: int, user_id: int) -> bool:
    allowed = approvers(rule, asker)
    if verb != "ok":
        allowed = allowed | {int(asker)}
    return int(user_id) in allowed


def waiting_on(rule: str, asker: int) -> str:
    if rule == "m":
        return f"<@{USERS.OGGERS}> or <@{USERS.HADIDAS}>"
    if rule == "a":
        return f"<@{int(asker)}> or <@{USERS.OGGERS}>"
    return f"<@{USERS.OGGERS}>"


# ---------------------------------------------------------------- state

def empty_state() -> dict:
    return {"channel_id": str(CHANNEL_ID), "message_id": None, "next_no": 1, "version": 0,
            "tasks": [], "history": []}


def load_state(path: str = None) -> dict:
    try:
        with open(path or STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        return empty_state()
    for key, value in empty_state().items():
        state.setdefault(key, value)
    return state


def save_state(state: dict, path: str = None) -> None:
    path = path or STATE_FILE
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


@contextlib.contextmanager
def locked(lock_path: str = None):
    """Exclusive lock across processes. Blocking, so the bot calls it from a thread."""
    import fcntl
    lock_path = lock_path or LOCK_FILE
    os.makedirs(os.path.dirname(lock_path), mode=0o700, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def find(state: dict, task_id) -> dict | None:
    task_id = str(task_id)
    return next((t for t in state["tasks"] if t["id"] == task_id), None)


def roll_off(state: dict) -> None:
    """Keep the newest KEEP_DONE done rows on the board; the rest go to history."""
    done = sorted((t for t in state["tasks"] if t["status"] == "done"), key=lambda t: t["updated"])
    old = done[:-KEEP_DONE] if len(done) > KEEP_DONE else []
    if old:
        ids = {t["id"] for t in old}
        state["tasks"] = [t for t in state["tasks"] if t["id"] not in ids]
        state["history"] = (state["history"] + old)[-KEEP_HISTORY:]


def _rule(value) -> str:
    value = str(value or "o")
    value = RULE_NAMES.get(value, value)
    if value not in RULES:
        raise ValueError(f"rule must be oggers, mods or asker, not {value!r}")
    return value


def _status(value) -> str:
    if value not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}, not {value!r}")
    return value


def apply_op(state: dict, op: str, args: dict, now: float = None) -> dict:
    """One board.py change. Returns the task touched (or None for show/init)."""
    now = time.time() if now is None else now
    if op == "add":
        task = find(state, args["id"])
        if task is None:
            task = {"no": state["next_no"], "id": str(args["id"]), "created": now}
            state["next_no"] += 1
            state["tasks"].append(task)
        task.update({
            "asker": str(int(args["asker"])),
            "what": str(args["what"])[:WHAT_CHARS],
            "status": _status(args.get("status") or "working"),
            "rule": _rule(args.get("rule")),
            "note": str(args.get("note") or "")[:NOTE_CHARS],
            "link": str(args["link"]) if args.get("link") else task.get("link"),
            "updated": now,
        })
    elif op == "set":
        task = find(state, args["id"])
        if task is None:
            raise ValueError(f"no task {args['id']} on the board (add it first)")
        if args.get("status"):
            task["status"] = _status(args["status"])
        if args.get("rule"):
            task["rule"] = _rule(args["rule"])
        if args.get("what"):
            task["what"] = str(args["what"])[:WHAT_CHARS]
        if "note" in args and args["note"] is not None:
            task["note"] = str(args["note"])[:NOTE_CHARS]
        if args.get("link"):
            task["link"] = str(args["link"])
        task["updated"] = now
    elif op == "remove":
        task = find(state, args["id"])
        state["tasks"] = [t for t in state["tasks"] if t["id"] != str(args["id"])]
    elif op in ("show", "init", "redraw"):
        task = None
    else:
        raise ValueError(f"unknown op {op!r}")
    roll_off(state)
    state["version"] = int(state.get("version", 0)) + 1
    return task


# ---------------------------------------------------------------- rendering

def _url(message_id) -> str:
    return f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{message_id}"


def _row(task: dict, waiting: bool) -> str:
    what = task["what"].replace("[", "(").replace("]", ")")
    line = f"**#{task['no']} [{what}]({_url(task['id'])})** · <@{task['asker']}>"
    small = []
    if waiting:
        small.append("needs " + waiting_on(task.get("rule", "o"), int(task["asker"])))
    if task.get("note"):
        small.append(task["note"])
    if task.get("link"):
        small.append(f"[{'plan' if waiting else 'result'} ↗]({_url(task['link'])})")
    return line + ("\n-# " + " · ".join(small) if small else "")


def _button(verb: str, task: dict) -> dict:
    label, style, emoji = {"ok": ("Approve", 3, "✅"), "no": ("Reject", 4, "✖️"),
                           "say": ("Reply", 2, "💬")}[verb]
    return {"type": 2, "style": style, "label": label, "emoji": {"name": emoji},
            "custom_id": custom_id(verb, task["id"], task.get("rule", "o"), task["asker"])}


def custom_id(verb: str, task_id, rule: str, asker) -> str:
    return f"cwb:{verb}:{int(task_id)}:{rule}:{int(asker)}"


def _count(components: list) -> int:
    n = 0
    for c in components:
        n += 1 + _count(c.get("components", []))
    return n


def _text_len(components: list) -> int:
    total = 0
    for c in components:
        total += len(c.get("content", "")) + _text_len(c.get("components", []))
    return total


def render(state: dict, now: float = None) -> list:
    """The board as raw CV2 components, kept inside Discord's limits (40 components, 4000
    characters). Rows that don't fit are dropped from the bottom of a group with a "+N more"
    line: done first, then the overflow waiting rows, working, blocked and received."""
    now = time.time() if now is None else now
    groups = {s: sorted((t for t in state["tasks"] if t["status"] == s),
                        key=lambda t: (t["updated"] if s == "done" else t["no"]),
                        reverse=(s == "done")) for s in STATUSES}
    groups["done"] = groups["done"][:KEEP_DONE]
    counts = " · ".join(f"{len(groups[s])} {s}" for s in STATUSES if groups[s])
    accent = next((ACCENTS[s] for s in STATUSES if groups[s]), ACCENTS["done"])
    header = ("## 📋 Vic's desk\n-# #bot-workshop requests · " + (counts or "nothing on")
              + f" · updated <t:{int(now)}:t>")
    footer = ("-# Buttons are for whoever the row needs; anyone else gets a private note. "
              "Plans and answers still come as replies.")

    waiting = groups["waiting"]
    button_rows = [(task, _row(task, True)) for task in waiting[:MAX_BUTTON_ROWS]]
    # Text blocks that can lose rows, in the order they give them up.
    blocks = {s: [_row(t, False) for t in groups[s]] for s in ("received", "blocked", "working", "done")}
    blocks["overflow"] = [_row(t, True) for t in waiting[MAX_BUTTON_ROWS:]]
    dropped = {k: 0 for k in blocks}

    def block_text(key):
        rows = blocks[key]
        if not rows and not dropped[key]:
            return ""
        if key == "overflow":
            head, tail = "", "\n-# no room for more buttons: answer these on their cards"
        else:
            head, tail = HEADINGS[key] + "\n", ""
        more = f"\n-# +{dropped[key]} more" if dropped[key] else ""
        return head + "\n".join(rows) + more + tail

    def total():
        return (len(header) + len(footer) + len(HEADINGS["waiting"]) + 1
                + sum(len(r) for _, r in button_rows) + sum(len(block_text(k)) for k in blocks))

    for key in ("done", "overflow", "working", "blocked", "received"):
        while total() > MAX_TEXT and blocks[key]:
            blocks[key].pop()
            dropped[key] += 1

    items = [{"type": 10, "content": header}]
    if not state["tasks"]:
        items += [{"type": 14, "divider": True, "spacing": 1},
                  {"type": 10, "content": "Nothing on right now. Ping Vic here to add something."}]
    if button_rows:
        items.append({"type": 14, "divider": True, "spacing": 2})
        for i, (task, row) in enumerate(button_rows):
            items.append({"type": 10, "content": (HEADINGS["waiting"] + "\n" if i == 0 else "") + row})
            items.append({"type": 1, "components": [_button(v, task) for v in ("ok", "no", "say")]})
        if block_text("overflow"):
            items.append({"type": 10, "content": block_text("overflow")})
    for key in ("received", "blocked", "working", "done"):
        if block_text(key):
            items.append({"type": 14, "divider": True, "spacing": 2})
            items.append({"type": 10, "content": block_text(key)})
    items += [{"type": 14, "divider": True, "spacing": 1}, {"type": 10, "content": footer}]
    components = [{"type": 17, "accent_color": accent, "components": items}]
    assert _count(components) <= MAX_COMPONENTS, _count(components)
    return components


def payload(state: dict, now: float = None) -> dict:
    return {"flags": 1 << 15, "components": render(state, now),
            "allowed_mentions": {"parse": []}}


# ---------------------------------------------------------------- presses

def write_event(event: dict, events_dir: str = None) -> str:
    events_dir = events_dir or EVENTS_DIR
    os.makedirs(events_dir, mode=0o700, exist_ok=True)
    path = os.path.join(events_dir, f"{event['interaction']}.json")
    tmp = os.path.join(events_dir, f".{event['interaction']}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(event, f, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def record_press(verb: str, task_id: int, user_id: int, interaction_id: int, text: str = "",
                 state_path: str = None, lock_path: str = None, events_dir: str = None,
                 now: float = None):
    """Apply a press to the board and write its event. Returns (ok, message_for_presser).
    Blocking (file lock), so the bot runs it in a thread."""
    now = time.time() if now is None else now
    with locked(lock_path):
        state = load_state(state_path)
        task = find(state, task_id)
        if task is None:
            return False, "That request isn't on the board any more."
        if task["status"] != "waiting":
            return False, f"#{task['no']} isn't waiting on a go any more (it's {task['status']})."
        # The row leaves 🟠 at once (so a second press can't land) and shows it was received;
        # the session moves it on to working, done or back to waiting once it has acted.
        who = f"<@{int(user_id)}>"
        task["status"] = "received"
        if verb == "ok":
            task["note"] = f"⏳ Go from {who}, processing…"
            reply = f"Got your go on #{task['no']}. Vic's picking it up."
        elif verb == "no":
            task["note"] = f"⏳ No from {who}, processing…"
            reply = f"Got your no on #{task['no']}. Vic will leave it."
        else:
            task["note"] = f"⏳ Reply from {who}, processing…"
            reply = f"Sent your reply on #{task['no']} to Vic."
        task["updated"] = now
        roll_off(state)
        state["version"] = int(state.get("version", 0)) + 1
        save_state(state, state_path)
        write_event({
            "interaction": str(interaction_id), "action": {"ok": "approve", "no": "reject", "say": "reply"}[verb],
            "task": task["id"], "no": task["no"], "what": task["what"], "asker": task["asker"],
            "user_id": str(int(user_id)), "text": text or "", "ts": now,
        }, events_dir)
    return True, reply


async def redraw(client) -> None:
    """Edit the board to match the state file. Re-checks the version afterwards, so a
    board.py update landing in between can't leave an older picture showing."""
    from discord.http import Route
    for _ in range(3):
        state = load_state()
        if not state.get("message_id"):
            return
        version = state.get("version")
        try:
            await client.http.request(
                Route("PATCH", "/channels/{channel_id}/messages/{message_id}",
                      channel_id=int(state["channel_id"]), message_id=int(state["message_id"])),
                json=payload(state))
        except Exception:
            logger.warning("task board: could not redraw", exc_info=True)
            return
        if load_state().get("version") == version:
            return


async def _log(client, text: str) -> None:
    try:
        channel = client.get_channel(CHANNELS.BOT_USAGE_LOG)
        if channel is not None:
            await channel.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.debug("task board: could not log", exc_info=True)


async def handle_press(interaction, verb: str, task_id: int, rule: str, asker: int, text: str = "") -> None:
    ok, reply = await asyncio.to_thread(
        record_press, verb, task_id, interaction.user.id, interaction.id, text)
    await interaction.response.send_message(reply, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())
    if ok:
        await redraw(interaction.client)
        await _log(interaction.client, f"task board: {interaction.user} pressed {verb} on {task_id}"
                   + (f" with: {text[:300]}" if text else ""))


async def refuse(interaction, verb: str, rule: str, asker: int) -> None:
    who = waiting_on(rule, asker)
    if verb == "ok":
        msg = f"This one's waiting on {who} to approve it."
    else:
        msg = f"Only {who} or whoever asked can answer this one. Ping Vic in the channel instead."
    await interaction.response.send_message(msg, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


class _BoardButton(discord.ui.DynamicItem[discord.ui.Button], template=r"$"):
    verb = ""
    look = ("", discord.ButtonStyle.secondary, "")

    def __init__(self, task_id=0, rule="o", asker=0):
        self.task_id, self.rule, self.asker = int(task_id), str(rule), int(asker)
        label, style, emoji = self.look
        super().__init__(discord.ui.Button(label=label, style=style, emoji=emoji,
                                           custom_id=custom_id(self.verb, self.task_id, self.rule, self.asker)))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["tid"], match["rule"], match["asker"])

    async def callback(self, interaction):
        if not may_press(self.verb, self.rule, self.asker, interaction.user.id):
            await refuse(interaction, self.verb, self.rule, self.asker)
            return
        await handle_press(interaction, self.verb, self.task_id, self.rule, self.asker)


class ApproveButton(_BoardButton, template=r"cwb:ok:(?P<tid>\d+):(?P<rule>[oma]):(?P<asker>\d+)"):
    verb = "ok"
    look = ("Approve", discord.ButtonStyle.success, "✅")


class RejectButton(_BoardButton, template=r"cwb:no:(?P<tid>\d+):(?P<rule>[oma]):(?P<asker>\d+)"):
    verb = "no"
    look = ("Reject", discord.ButtonStyle.danger, "✖️")


class ReplyModal(discord.ui.Modal):
    def __init__(self, task_id: int, rule: str, asker: int):
        super().__init__(title="Reply to Vic", timeout=900)
        self.task_id, self.rule, self.asker = task_id, rule, asker
        self.text = discord.ui.TextInput(label="Your reply", style=discord.TextStyle.paragraph,
                                         max_length=1000, placeholder="e.g. go, but cap it at 10k")
        self.add_item(self.text)

    async def on_submit(self, interaction):
        if not may_press("say", self.rule, self.asker, interaction.user.id):
            await refuse(interaction, "say", self.rule, self.asker)
            return
        await handle_press(interaction, "say", self.task_id, self.rule, self.asker, str(self.text.value or ""))


class ReplyButton(_BoardButton, template=r"cwb:say:(?P<tid>\d+):(?P<rule>[oma]):(?P<asker>\d+)"):
    verb = "say"
    look = ("Reply", discord.ButtonStyle.secondary, "💬")

    async def callback(self, interaction):
        if not may_press(self.verb, self.rule, self.asker, interaction.user.id):
            await refuse(interaction, self.verb, self.rule, self.asker)
            return
        await interaction.response.send_modal(ReplyModal(self.task_id, self.rule, self.asker))


BUTTONS = (ApproveButton, RejectButton, ReplyButton)
