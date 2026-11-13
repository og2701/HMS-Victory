"""Discord control card for the persona bot: the full Vic with memory and the persona.

The persona bot is its own service on this box (systemd unit ``persona-bot``) and
logs in with the same token as this bot. Its card sits under the Direct Replies
card in the chatbot controller thread. It starts, stops and restarts the service,
and sets each channel's mode through the console API on 127.0.0.1:8765, which is
the same API the Tailscale console uses, so both always show the same Vic.

While the persona bot is on in a server, Direct Replies stand down there, so a
tag never gets two answers from two different Vics.

Passes let someone else switch it on once: the card sends them a DM button that
turns Vic on in a chosen channel for a chosen time, through the same sleep timer.
Once used, the pass message links to the first thing Vic says in that session.
"""

import asyncio
import datetime
import logging
import os
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Set, Tuple

import aiohttp
import discord

import config
from config import CHANNELS, USERS
from lib.core.file_operations import atomic_write_json, load_json_file

logger = logging.getLogger(__name__)

PERSONA_SERVICE = "persona-bot"
PERSONA_API = os.environ.get("PERSONA_API", "http://127.0.0.1:8765")
# Read only while the service is stopped, to show the modes it will come back with.
PERSONA_RUNTIME_FILE = os.environ.get("PERSONA_RUNTIME_FILE", "/home/ubuntu/persona-bot/runtime.json")
CARD_STATE_FILE = os.path.join(config.JSON_DATA_DIR, "persona_card.json")
CUSTOM_ID_PREFIX = "persona_ctl:"
POLL_SECS = 15.0
SETTLE_SECS = 90.0

# Service states where the persona bot is up or about to be, so its channel modes still count.
RUNNING_STATES = {"active", "activating", "reloading", "deactivating"}
MODES = {"off": ("💤", "Off"), "mention": ("🏷️", "Mentions only"), "ambient": ("🌊", "Ambient")}
SLEEP_CHOICES = ((5, "5 min"), (10, "10 min"), (15, "15 min"), (30, "30 min"),
                 (60, "1 hour"), (120, "2 hours"), (240, "4 hours"))
PASSES_FILE = os.path.join(config.JSON_DATA_DIR, "persona_passes.json")
PASS_TTL_SECS = 24 * 3600  # an unused pass runs out after this
PASS_KEEP_SECS = 7 * 24 * 3600  # used, taken back and run-out passes are forgotten after this
# The first-message link on a used pass. The persona bot posts as this same account, so
# its replies reach on_message here; what this process posted itself is told apart by
# the message ids send_message hands back (see looks_like_persona_reply).
OWN_SEND_GRACE_SECS = 3.0  # the gateway can beat the HTTP response for our own sends
FIRST_MSG_SETTLE_SECS = 30.0  # after the session, before "Vic didn't say anything" is final
FIRST_MSG_GIVE_UP_SECS = 3600.0  # stop trying once history can't be read for this long after
FIRST_MSG_HISTORY_LIMIT = 500
OWN_SENT_LIMIT = 500
CLOSED_PASS_REPLIES = {
    "used": "You've already used this pass.",
    "revoked": "This pass was taken back.",
    "expired": "This pass ran out before it was used.",
}


@dataclass
class PersonaState:
    service: str = "unknown"  # systemd ActiveState
    started_mono: int = 0  # ActiveEnterTimestampMonotonic, in microseconds
    api_up: bool = False
    channels: Dict[int, str] = field(default_factory=dict)  # mode per configured channel
    timers: Dict[int, float] = field(default_factory=dict)  # sleep timer expiry per channel

    @property
    def running(self) -> bool:
        return self.service in RUNNING_STATES

    @property
    def since(self) -> Optional[int]:
        if not self.started_mono:
            return None
        return int(time.time() - (time.monotonic() - self.started_mono / 1_000_000))

    def signature(self) -> tuple:
        return (self.service, self.started_mono, self.api_up,
                tuple(self.channels.items()), tuple(sorted(self.timers.items())))


_state = PersonaState()
_client: Optional[discord.Client] = None
_card_message: Optional[discord.Message] = None
_poll_task: Optional[asyncio.Task] = None
_wake = asyncio.Event()
_settle_until = 0.0  # poll fast and ride over the stop half of a restart until then
_last_live: frozenset = frozenset()
_pass_lock = asyncio.Lock()
_own_sent: "OrderedDict[int, None]" = OrderedDict()  # ids of messages this process sent
_watches: Dict[int, list] = {}  # channel id -> [(pass id, after snowflake, before snowflake)]
_swept: Set[str] = set()  # passes whose channel history was read since this process started


def parse_systemctl_show(text: str) -> Tuple[str, int]:
    """ActiveState and ActiveEnterTimestampMonotonic from ``systemctl show`` output."""
    values = dict(line.split("=", 1) for line in (text or "").splitlines() if "=" in line)
    try:
        started = int(values.get("ActiveEnterTimestampMonotonic") or 0)
    except ValueError:
        started = 0
    return (values.get("ActiveState") or "unknown").strip(), started


async def _run(*args: str, timeout: float = 20.0) -> Tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode(errors="replace")
    except (OSError, asyncio.TimeoutError) as e:
        return 1, str(e)


async def _api(method: str, path: str, payload: Optional[dict] = None) -> Optional[dict]:
    """Call the persona console API; None when it's down or says no."""
    try:
        timeout = aiohttp.ClientTimeout(total=10 if method != "GET" else 4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, PERSONA_API + path, json=payload) as resp:
                if resp.status >= 400:
                    logger.warning("Persona API %s %s returned %s", method, path, resp.status)
                    return None
                return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None


def _modes_from(runtime: dict) -> Dict[int, str]:
    out = {}
    for cid, cfg in ((runtime or {}).get("channels") or {}).items():
        if str(cid).isdigit():
            mode = str((cfg or {}).get("mode") or "off")
            out[int(cid)] = mode if mode in MODES else "off"
    return out


async def refresh_state() -> PersonaState:
    global _state
    rc, out = await _run("systemctl", "show", PERSONA_SERVICE,
                         "--property=ActiveState,ActiveEnterTimestampMonotonic")
    service, started = parse_systemctl_show(out) if rc == 0 else ("unknown", 0)
    if service in ("inactive", "failed") and time.time() < _settle_until:
        service = "activating"  # between the stop and the start of a restart
    new = PersonaState(service=service, started_mono=started)
    runtime = await _api("GET", "/api/runtime") if service == "active" else None
    if runtime is not None:
        new.api_up = True
        new.channels = _modes_from(runtime)
        timers = (await _api("GET", "/api/sleep_timer") or {}).get("timers") or {}
        new.timers = {int(cid): float(t.get("expires_at") or 0) for cid, t in timers.items() if str(cid).isdigit()}
    elif new.running:
        # Starting up or mid-restart: it comes back with the modes it had.
        new.channels, new.timers = dict(_state.channels), dict(_state.timers)
    else:
        try:
            new.channels = _modes_from(load_json_file(PERSONA_RUNTIME_FILE))
        except (OSError, ValueError):
            new.channels = dict(_state.channels)
    _state = new
    return new


def _channel_guild_id(channel_id: int) -> Optional[int]:
    channel = _client.get_channel(channel_id) if _client else None
    guild = getattr(channel, "guild", None)
    return getattr(guild, "id", None)


def live_guilds(state: Optional[PersonaState] = None) -> Set[int]:
    """Servers where the persona bot is running with at least one channel on."""
    state = state or _state
    if not state.running:
        return set()
    return {
        gid for cid, mode in state.channels.items()
        if mode != "off" and (gid := _channel_guild_id(cid)) is not None
    }


def persona_live(guild_id: Optional[int]) -> bool:
    """True when Direct Replies should leave a tag in this server to the persona bot."""
    return guild_id is not None and guild_id in live_guilds()


def _custom_ids(components: Optional[Iterable]) -> Iterable[str]:
    for component in components or ():
        custom_id = getattr(component, "custom_id", None)
        if isinstance(custom_id, str):
            yield custom_id
        children = getattr(component, "children", None)
        if isinstance(children, (list, tuple)):
            yield from _custom_ids(children)
        accessory = getattr(component, "accessory", None)
        if accessory is not None:
            yield from _custom_ids([accessory])


def is_persona_card(message: discord.Message) -> bool:
    """So the Direct Replies card's finder never mistakes this card for its own."""
    saved = load_json_file(CARD_STATE_FILE).get("message_id")
    if saved and str(saved) == str(getattr(message, "id", "")):
        return True
    components = getattr(message, "components", None)
    if not isinstance(components, (list, tuple)):
        return False
    return any(cid.startswith(CUSTOM_ID_PREFIX) for cid in _custom_ids(components))


async def set_channel_mode(channel_id: int, mode: str) -> bool:
    runtime = await _api("GET", "/api/runtime")
    if runtime is None:
        return False
    channels = dict(runtime.get("channels") or {})
    cfg = dict(channels.get(str(channel_id)) or {})
    cfg["mode"] = mode
    channels[str(channel_id)] = cfg
    # The API replaces the whole channel map, so every other channel goes back as it was.
    return await _api("POST", "/api/runtime", {"channels": channels}) is not None


def _channel_name(channel_id: int) -> str:
    channel = _client.get_channel(channel_id) if _client else None
    return f"#{channel.name}" if channel is not None else f"#{channel_id}"


def _minutes_label(minutes: int) -> str:
    return dict(SLEEP_CHOICES).get(int(minutes), f"{int(minutes)} min")


# ---------- Passes ----------

def load_passes() -> Dict[str, dict]:
    try:
        data = load_json_file(PASSES_FILE)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_passes(passes: Dict[str, dict]) -> None:
    cutoff = time.time() - PASS_KEEP_SECS
    atomic_write_json(PASSES_FILE, {
        pid: p for pid, p in passes.items()
        if pass_status(p) == "open" or float(p.get("closed_at") or p.get("expires_at") or 0) > cutoff
    })


def pass_status(p: dict, now: Optional[float] = None) -> str:
    """open, used, revoked or expired. A pass past its time reads as expired before the sweep marks it."""
    status = p.get("status") or "open"
    if status == "open" and float(p.get("expires_at") or 0) <= (now or time.time()):
        return "expired"
    return status


def _pass_text(p: dict) -> str:
    lead = f"<@{p['user_id']}> " if p.get("delivery") == "channel" else ""
    return (
        f"{lead}🎟️ **{p['granted_by_name']}** gave you a one-time pass to switch Vic on in <#{p['channel_id']}> "
        f"for **{_minutes_label(p['minutes'])}**."
    )


def _pass_view(pid: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(PassButton(pid))
    return view


async def give_pass(client: discord.Client, granter, user, channel_id: int, minutes: int) -> Tuple[str, dict]:
    """DM someone a one-time pass, or post it in the channel tagging them when their DMs are closed."""
    now = time.time()
    pid = secrets.token_hex(4)
    p = {
        "user_id": str(user.id), "user_name": getattr(user, "display_name", str(user)),
        "channel_id": str(channel_id), "minutes": int(minutes),
        "granted_by": str(granter.id), "granted_by_name": getattr(granter, "display_name", str(granter)),
        "created_at": now, "expires_at": now + PASS_TTL_SECS, "status": "open", "delivery": "dm",
    }
    footer = f"\nPress the button whenever you want it. It runs out <t:{int(p['expires_at'])}:R> if you don't."
    try:
        message = await user.send(_pass_text(p) + footer, view=_pass_view(pid))
    except discord.HTTPException:
        p["delivery"] = "channel"
        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        message = await channel.send(_pass_text(p) + footer, view=_pass_view(pid),
                                     allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=[user]))
    p["message_channel_id"], p["message_id"] = str(message.channel.id), str(message.id)
    async with _pass_lock:
        passes = load_passes()
        passes[pid] = p
        _save_passes(passes)
    logger.info("Persona pass %s: %s gave %s %d min in %s (%s)", pid, granter, user, minutes, channel_id, p["delivery"])
    return pid, p


async def _close_pass_message(p: dict, note: str) -> None:
    """Swap the pass button for what happened to it."""
    if _client is None or not p.get("message_id"):
        return
    try:
        channel = _client.get_partial_messageable(int(p["message_channel_id"]))
        await channel.get_partial_message(int(p["message_id"])).edit(content=_pass_text(p) + "\n" + note, view=None)
    except (discord.HTTPException, ValueError) as e:
        logger.debug("Could not update persona pass message: %s", e)


def _used_note(p: dict) -> str:
    """The line under a used pass: when, where, until when, and what Vic said first."""
    note = (f"✅ Used <t:{int(float(p['closed_at']))}:R>. Vic is on in <#{p['channel_id']}> "
            f"until <t:{int(float(p['session_until']))}:t>.")
    if p.get("first_msg_url"):
        note += f" · [first message](<{p['first_msg_url']}>)"
    elif p.get("first_msg_done"):
        note += " Vic didn't say anything this session."
    return note


# ---------- Vic's first message in a pass session ----------

def _snowflake(ts: float, high: bool = False) -> int:
    return discord.utils.time_snowflake(datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc), high=high)


def _watching(p: dict) -> bool:
    """A used pass still waiting to learn what Vic said first. Passes from before this
    feature have no first_msg_id key, so they're never watched."""
    return (p.get("status") == "used" and "first_msg_id" in p and not p.get("first_msg_id")
            and not p.get("first_msg_done") and p.get("session_until") is not None)


def _load_watches(passes: Optional[Dict[str, dict]] = None) -> None:
    global _watches
    watches: Dict[int, list] = {}
    for pid, p in (load_passes() if passes is None else passes).items():
        if _watching(p):
            watches.setdefault(int(p["channel_id"]), []).append(
                (pid, _snowflake(float(p["closed_at"])), _snowflake(float(p["session_until"]), high=True)))
    _watches = watches


def _remember_own_send(message_id) -> None:
    try:
        _own_sent[int(message_id)] = None
    except (TypeError, ValueError):
        return
    while len(_own_sent) > OWN_SENT_LIMIT:
        _own_sent.popitem(last=False)


def _track_own_sends(client: discord.Client) -> None:
    """Note the id of every message this process posts, so the persona bot's are the rest.

    Every channel.send and message.reply goes through HTTPClient.send_message.
    """
    http = getattr(client, "http", None)
    original = getattr(http, "send_message", None)
    if not callable(original) or getattr(original, "_persona_tracked", False) is True:
        return

    async def send_message(*args, **kwargs):
        data = await original(*args, **kwargs)
        if isinstance(data, dict):
            _remember_own_send(data.get("id"))
        return data

    send_message._persona_tracked = True
    http.send_message = send_message


def looks_like_persona_reply(message: discord.Message) -> bool:
    """Plain chat from this account that this process didn't post: what the persona bot sends.

    This bot's own posts are slash-command responses, embeds, Components V2 cards, files
    and buttons, and the plain-text ones it sends are in ``_own_sent``. The persona bot
    only ever sends text, an emoji or a sticker, as a plain message or a reply.
    """
    user = getattr(_client, "user", None)
    if user is None or getattr(message.author, "id", None) != user.id:
        return False
    if message.id in _own_sent:
        return False
    if getattr(message, "type", None) not in (discord.MessageType.default, discord.MessageType.reply):
        return False
    if getattr(message, "interaction_metadata", None) is not None or getattr(message, "webhook_id", None) is not None:
        return False
    if getattr(getattr(message, "flags", None), "components_v2", False) is True:
        return False
    if message.components or message.attachments:
        return False
    if any(getattr(e, "type", "rich") == "rich" for e in message.embeds or ()):
        return False  # link previews are fine, embeds this bot builds are not
    return bool((message.content or "").strip() or getattr(message, "stickers", None))


def _jump_url(message: discord.Message, channel_id: int) -> str:
    url = getattr(message, "jump_url", None)
    if isinstance(url, str) and url:
        return url
    return f"https://discord.com/channels/{_channel_guild_id(channel_id) or '@me'}/{channel_id}/{message.id}"


async def _record_first_message(pid: str, message_id: int, url: str) -> bool:
    """Link the pass to this message and stop watching."""
    async with _pass_lock:
        passes = load_passes()
        p = passes.get(pid)
        if p is None or not _watching(p):
            return False
        p.update(first_msg_id=str(message_id), first_msg_url=url)
        _save_passes(passes)
        _load_watches(passes)
    _swept.discard(pid)
    logger.info("Persona pass %s: Vic's first message is %s", pid, message_id)
    await _close_pass_message(p, _used_note(p))
    try:
        await refresh_and_update(force=True)  # the card's pass list links it too
    except Exception:
        logger.debug("persona card refresh after first message failed", exc_info=True)
    return True


async def _consider_live(message: discord.Message, pids: list) -> None:
    await asyncio.sleep(OWN_SEND_GRACE_SECS)
    if message.id in _own_sent:
        return
    for pid in pids:
        await _record_first_message(pid, message.id, _jump_url(message, message.channel.id))


def note_own_message(message: discord.Message) -> None:
    """Called for every message this account posts; catches Vic's first word after a pass.

    Only passes whose channel history this process has already read (or that were used
    in this process) take a live message, so after a restart the sweep can still find an
    earlier one first.
    """
    try:
        watches = _watches.get(message.channel.id)
        if not watches or not looks_like_persona_reply(message):
            return
        pids = [pid for pid, after, before in watches if pid in _swept and after < message.id <= before]
        if pids:
            asyncio.create_task(_consider_live(message, pids))
    except Exception:
        logger.debug("persona first-message check failed", exc_info=True)


async def _first_in_history(channel_id: int, after: int, before: int) -> Optional[discord.Message]:
    channel = _client.get_channel(channel_id) or await _client.fetch_channel(channel_id)
    async for m in channel.history(after=discord.Object(id=after), before=discord.Object(id=before + 1),
                                   limit=FIRST_MSG_HISTORY_LIMIT, oldest_first=True):
        if looks_like_persona_reply(m):
            return m
    return None


async def _finish_watch(pid: str, say_so: bool) -> None:
    """Vic said nothing this session: say so on the pass, or just stop watching."""
    async with _pass_lock:
        passes = load_passes()
        p = passes.get(pid)
        if p is None or not _watching(p):
            return
        p["first_msg_done"] = True
        _save_passes(passes)
        _load_watches(passes)
    _swept.discard(pid)
    if say_so:
        logger.info("Persona pass %s: Vic didn't say anything", pid)
        await _close_pass_message(p, _used_note(p))


async def sweep_first_messages(now: Optional[float] = None) -> None:
    """Fill in from channel history what live events missed, and close finished sessions.

    A pending pass is read once after a restart, and once more when its session is over.
    """
    if _client is None:
        return
    now = now or time.time()
    passes = load_passes()
    _load_watches(passes)
    for pid, p in passes.items():
        if not _watching(p):
            continue
        until = float(p["session_until"])
        over = now >= until + FIRST_MSG_SETTLE_SECS
        if pid in _swept and not over:
            continue
        channel_id = int(p["channel_id"])
        try:
            found = await _first_in_history(channel_id, _snowflake(float(p["closed_at"])), _snowflake(until, high=True))
        except (discord.HTTPException, ValueError) as e:
            logger.debug("Could not read %s for persona pass %s: %s", channel_id, pid, e)
            _swept.add(pid)  # live messages count meanwhile; retry once the session is over
            if now >= until + FIRST_MSG_GIVE_UP_SECS:
                await _finish_watch(pid, say_so=False)
            continue
        _swept.add(pid)
        if found is not None:
            await _record_first_message(pid, found.id, _jump_url(found, channel_id))
        elif over:
            await _finish_watch(pid, say_so=True)


async def _ensure_running(timeout: float = 60.0) -> Optional[PersonaState]:
    """Start the persona bot if it's stopped and wait until it's logged in; None if it doesn't come up."""
    state = await refresh_state()
    if not state.running:
        ok, _ = await _service_action("start")
        if not ok:
            return None
    deadline = time.time() + timeout
    while True:
        if state.service == "active" and state.api_up:
            status = await _api("GET", "/api/status")
            if status and status.get("ready"):
                return state
        if time.time() >= deadline:
            return None
        await asyncio.sleep(2)
        state = await refresh_state()


async def use_pass(pid: str, user_id: int) -> Tuple[bool, str]:
    """Switch Vic on for the pass holder, starting it first if it's stopped.

    The pass stays unused when Vic can't be switched on.
    """
    async with _pass_lock:
        passes = load_passes()
        p = passes.get(pid)
        if p is None or p["user_id"] != str(user_id):
            return False, "This pass isn't yours to use."
        status = pass_status(p)
        if status != "open":
            return False, CLOSED_PASS_REPLIES[status]
        channel_id = int(p["channel_id"])
        state = await _ensure_running()
        if state is None:
            return False, "Vic didn't come up just now, so your pass is still here. Try again in a minute."
        if state.channels.get(channel_id, "off") != "off":
            return False, f"Vic's already on in <#{channel_id}>, so your pass is still here for later."
        result = await _api("POST", "/api/sleep_timer", {
            "channel_id": str(channel_id), "minutes": int(p["minutes"]), "active_mode": "ambient",
        })
        if result is None:
            return False, "Couldn't reach Vic just now, so your pass is still here. Try again in a minute."
        now = time.time()
        until = int(float(result.get("expires_at") or now + int(p["minutes"]) * 60))
        # first_msg_id stays None until Vic says something there (note_own_message, sweep_first_messages).
        p.update(status="used", closed_at=now, session_until=until, first_msg_id=None)
        _save_passes(passes)
        _load_watches(passes)
        _swept.add(pid)  # nothing in history yet to miss
    logger.info("Persona pass %s used by %s: Vic on in %s until %s", pid, user_id, channel_id, until)
    await _close_pass_message(p, _used_note(p))
    await refresh_and_update(force=True)  # Direct Replies stand down straight away
    return True, f"🫥 Vic is on in <#{channel_id}> until <t:{until}:t>."


async def revoke_pass(pid: str) -> bool:
    async with _pass_lock:
        passes = load_passes()
        p = passes.get(pid)
        if p is None or pass_status(p) != "open":
            return False
        p.update(status="revoked", closed_at=time.time())
        _save_passes(passes)
    logger.info("Persona pass %s taken back", pid)
    await _close_pass_message(p, "❌ This pass was taken back.")
    return True


async def expire_passes() -> bool:
    """Mark passes that ran out unused, and say so on their messages."""
    async with _pass_lock:
        passes = load_passes()
        expired = [p for p in passes.values() if (p.get("status") or "open") == "open" and pass_status(p) == "expired"]
        for p in expired:
            p.update(status="expired", closed_at=time.time())
        if expired:
            _save_passes(passes)
    for p in expired:
        await _close_pass_message(p, "⌛ This pass ran out before it was used.")
    return bool(expired)


class PassButton(discord.ui.DynamicItem[discord.ui.Button], template=r"persona_pass:(?P<pid>[0-9a-f]{8})"):
    """The holder's 'Turn Vic on' button. Dynamic, so it still works after a restart."""

    def __init__(self, pid: str):
        self.pid = pid
        super().__init__(discord.ui.Button(
            label="Turn Vic on", emoji="🎟️", style=discord.ButtonStyle.success, custom_id=f"persona_pass:{pid}",
        ))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["pid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        p = load_passes().get(self.pid)
        if p is None:
            await interaction.response.send_message("This pass doesn't exist any more.", ephemeral=True)
        elif p["user_id"] != str(interaction.user.id):
            await interaction.response.send_message("This pass belongs to someone else.", ephemeral=True)
        elif pass_status(p) != "open":
            await interaction.response.send_message(CLOSED_PASS_REPLIES[pass_status(p)], ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Switch Vic on in <#{p['channel_id']}> for {_minutes_label(p['minutes'])} now? You can only do this once.",
                view=_UsePassView(self.pid), ephemeral=True)


class _UsePassView(discord.ui.View):
    def __init__(self, pid: str):
        super().__init__(timeout=120)
        self.pid = pid
        go = discord.ui.Button(label="Turn it on", emoji="🫥", style=discord.ButtonStyle.success)
        go.callback = self._go
        wait = discord.ui.Button(label="Not yet", style=discord.ButtonStyle.secondary)
        wait.callback = self._wait
        self.add_item(go)
        self.add_item(wait)

    async def _go(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="⏳ Switching Vic on… (up to a minute if it has to start up)", view=None)
        _, reply = await use_pass(self.pid, interaction.user.id)
        await interaction.edit_original_response(content=reply)
        self.stop()

    async def _wait(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="No problem, your pass is still there.", view=None)
        self.stop()


class _GivePassView(discord.ui.View):
    """Ephemeral form behind the card's Give a pass button."""

    def __init__(self):
        super().__init__(timeout=300)
        self.who = discord.ui.UserSelect(placeholder="Who gets it?")
        self.where = discord.ui.ChannelSelect(placeholder="Which channel?", channel_types=[discord.ChannelType.text])
        self.length = discord.ui.Select(placeholder="How long?", options=[
            discord.SelectOption(label=label, value=str(minutes), default=minutes == 10)
            for minutes, label in SLEEP_CHOICES
        ])
        send = discord.ui.Button(label="Send pass", emoji="🎟️", style=discord.ButtonStyle.success)
        for item, callback in ((self.who, self._picked), (self.where, self._picked),
                               (self.length, self._picked), (send, self._send)):
            item.callback = callback
            self.add_item(item)

    async def _picked(self, interaction: discord.Interaction):
        await interaction.response.defer()

    async def _send(self, interaction: discord.Interaction):
        if not self.who.values or not self.where.values:
            await interaction.response.send_message("Pick who gets it and which channel first.", ephemeral=True)
            return
        user, channel = self.who.values[0], self.where.values[0]
        if getattr(user, "bot", False):
            await interaction.response.send_message("Bots can't use passes.", ephemeral=True)
            return
        minutes = int(self.length.values[0]) if self.length.values else 10
        await interaction.response.edit_message(content="⏳ Sending the pass…", view=None)
        try:
            _, p = await give_pass(interaction.client, interaction.user, user, channel.id, minutes)
        except discord.HTTPException as e:
            await interaction.edit_original_response(content=f"❌ Couldn't deliver the pass: {e}")
            return
        where = "by DM" if p["delivery"] == "dm" else f"in <#{channel.id}>, because their DMs are closed"
        await interaction.edit_original_response(
            content=f"✅ Sent <@{user.id}> a pass for <#{channel.id}>, {_minutes_label(minutes)}, {where}. "
                    f"It runs out <t:{int(p['expires_at'])}:R> if unused.")
        self.stop()
        await refresh_and_update(force=True)


class RevokePassSelect(discord.ui.Select):
    def __init__(self, open_passes: list):
        options = [
            discord.SelectOption(
                label=f"{p.get('user_name') or p['user_id']} · {_channel_name(int(p['channel_id']))} · {_minutes_label(p['minutes'])}"[:100],
                value=pid, emoji="❌")
            for pid, p in open_passes[:25]
        ]
        super().__init__(custom_id=f"{CUSTOM_ID_PREFIX}revoke", placeholder="Take back a pass…", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        _remember_card(interaction)
        await revoke_pass(self.values[0])
        await refresh_and_update(force=True)


class ChannelModeSelect(discord.ui.Select):
    def __init__(self, channel_id: int, mode: str, has_timer: bool):
        name = _channel_name(channel_id)[:40]
        options = [
            discord.SelectOption(label=f"{name} · {label}", value=key, emoji=emoji, default=key == mode)
            for key, (emoji, label) in MODES.items()
        ]
        options += [
            discord.SelectOption(label=f"{name} · On, then off in {label}", value=f"sleep:{minutes}", emoji="😴")
            for minutes, label in SLEEP_CHOICES
        ]
        if has_timer:
            options.append(discord.SelectOption(label=f"{name} · Stay on (cancel sleep timer)", value="keep", emoji="⏰"))
        super().__init__(custom_id=f"{CUSTOM_ID_PREFIX}mode:{channel_id}", options=options)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        await interaction.response.defer()
        if choice.startswith("sleep:"):
            ok = await _api("POST", "/api/sleep_timer",
                            {"channel_id": str(self.channel_id), "minutes": int(choice.split(":", 1)[1])}) is not None
        elif choice == "keep":
            ok = await _api("DELETE", "/api/sleep_timer", {"channel_id": str(self.channel_id)}) is not None
        else:
            ok = await set_channel_mode(self.channel_id, choice)
        logger.info("Persona card: %s set %s to %s (%s)", interaction.user, self.channel_id, choice, "ok" if ok else "failed")
        _remember_card(interaction)
        await refresh_and_update(force=True)
        if not ok:
            await interaction.followup.send("❌ Couldn't reach Vic's console API, so nothing changed.", ephemeral=True)


async def _service_action(action: str) -> Tuple[bool, str]:
    global _settle_until
    _settle_until = time.time() + SETTLE_SECS if action in ("start", "restart") else 0.0
    rc, out = await _run("sudo", "-n", "systemctl", action, PERSONA_SERVICE, timeout=150.0)
    logger.info("Persona card: systemctl %s %s -> %s %s", action, PERSONA_SERVICE, rc, out.strip()[:200])
    if rc != 0:
        _settle_until = 0.0
    _wake.set()
    return rc == 0, out.strip()


class _ConfirmView(discord.ui.View):
    def __init__(self, action: str, label: str):
        super().__init__(timeout=60)
        self.action = action
        confirm = discord.ui.Button(label=label, style=discord.ButtonStyle.danger)
        confirm.callback = self._confirm
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(confirm)
        self.add_item(cancel)

    async def _confirm(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="⏳ Working on it…", view=None)
        ok, out = await _service_action(self.action)
        done = {"stop": "⏹️ Vic is stopped. Direct Replies are back on.",
                "restart": "🔄 Vic is restarting. It's back in about 10 seconds and answers anything sent meanwhile."}
        await interaction.edit_original_response(
            content=done[self.action] if ok else f"❌ `systemctl {self.action}` failed: {out[:300] or 'no output'}"
        )
        self.stop()

    async def _cancel(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Left it as it was.", view=None)
        self.stop()


class _CardButton(discord.ui.Button):
    def __init__(self, action: str, label: str, emoji: str, style: discord.ButtonStyle, disabled: bool = False):
        super().__init__(label=label, emoji=emoji, style=style, disabled=disabled,
                         custom_id=f"{CUSTOM_ID_PREFIX}{action}")
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        _remember_card(interaction)
        if self.action == "stop":
            await interaction.response.send_message(
                "Stop Vic? It stops replying everywhere until you start it again, and Direct Replies take back over.",
                view=_ConfirmView("stop", "Stop Vic"), ephemeral=True)
        elif self.action == "restart":
            await interaction.response.send_message(
                "Restart Vic? It's back in about 10 seconds.",
                view=_ConfirmView("restart", "Restart Vic"), ephemeral=True)
        elif self.action == "pass":
            await interaction.response.send_message(
                "🎟️ Who gets the pass, which channel, and for how long?", view=_GivePassView(), ephemeral=True)
        elif self.action == "start":
            await interaction.response.defer(ephemeral=True, thinking=True)
            ok, out = await _service_action("start")
            await interaction.followup.send(
                "▶️ Vic is starting, with the channel modes it had before." if ok
                else f"❌ `systemctl start` failed: {out[:300] or 'no output'}", ephemeral=True)
        else:
            await interaction.response.defer()
            await refresh_and_update(force=True)


class PersonaCardView(discord.ui.LayoutView):
    """Components V2 card. Its custom ids all start with ``persona_ctl:``."""

    def __init__(self, state: Optional[PersonaState] = None):
        super().__init__(timeout=None)
        self.state = state or _state
        self.build_ui()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != USERS.OGGERS:
            await interaction.response.send_message("⛔ Only Oggers can control HMS Victory.", ephemeral=True)
            return False
        return True

    def build_ui(self):
        s = self.state
        live = bool(live_guilds(s))
        up = s.service == "active" and s.api_up
        if up:
            status = "🟢 **Running**" + (f" since <t:{s.since}:R>" if s.since else "")
        elif s.service in ("activating", "reloading") or s.service == "active":
            status = "🟡 **Starting…**"
        elif s.service == "deactivating":
            status = "🟠 **Stopping…**"
        elif s.service == "failed":
            status = "🔴 **Crashed** *(press Start to bring it back)*"
        elif s.service == "unknown":
            status = "⚪ **Unknown** *(couldn't ask systemd)*"
        else:
            status = "🔴 **Stopped**"
        direct = ("⏸️ standing down while Vic is on" if live else "✅ answering tags as usual")
        card = discord.ui.Container(accent_colour=0x2ECC71 if live else (0x95A5A6 if s.running else 0xE74C3C))
        card.add_item(discord.ui.TextDisplay(
            "## 🫥 Vic · Persona Bot\n"
            "The full Vic, with its memory and persona. Same bot as the Tailscale console."
        ))
        card.add_item(discord.ui.Separator(visible=True))
        card.add_item(discord.ui.TextDisplay(f"⚙️ **Status:** {status}\n🏷️ **Direct Replies:** {direct}"))
        card.add_item(discord.ui.Separator(visible=True))

        lines = []
        for cid, mode in s.channels.items():
            emoji, label = MODES[mode]
            line = f"- <#{cid}> · {emoji} {label}"
            if cid in s.timers and mode != "off":
                line += f" · 😴 off <t:{int(s.timers[cid])}:R>"
            lines.append(line)
        heading = "### Channels" if s.running else "### Channels *(how it comes back)*"
        card.add_item(discord.ui.TextDisplay(heading + "\n" + ("\n".join(lines) or "- none set up in the console yet")))
        if up:
            for cid, mode in list(s.channels.items())[:5]:
                card.add_item(discord.ui.ActionRow(ChannelModeSelect(cid, mode, cid in s.timers)))
        now = time.time()
        passes = load_passes()
        open_passes = sorted(
            ((pid, p) for pid, p in passes.items() if pass_status(p, now) == "open"),
            key=lambda item: float(item[1]["expires_at"]),
        )
        used = sorted(
            (p for p in passes.values() if p.get("status") == "used" and float(p.get("closed_at") or 0) > now - 86400),
            key=lambda p: -float(p["closed_at"]),
        )[:3]
        if open_passes or used:
            lines = [
                f"- 🎟️ <@{p['user_id']}> · <#{p['channel_id']}> · {_minutes_label(p['minutes'])} · "
                f"runs out <t:{int(p['expires_at'])}:R>"
                for _, p in open_passes
            ] + [
                f"- ✅ <@{p['user_id']}> used one in <#{p['channel_id']}> <t:{int(p['closed_at'])}:R>"
                + (f" · [first message](<{p['first_msg_url']}>)" if p.get("first_msg_url") else "")
                for p in used
            ]
            card.add_item(discord.ui.Separator(visible=True))
            card.add_item(discord.ui.TextDisplay("### Passes\n" + "\n".join(lines)))
            if open_passes:
                card.add_item(discord.ui.ActionRow(RevokePassSelect(open_passes)))
        card.add_item(discord.ui.Separator(visible=True))
        card.add_item(discord.ui.ActionRow(
            _CardButton("start", "Start", "▶️", discord.ButtonStyle.success, disabled=s.running),
            _CardButton("stop", "Stop", "⏹️", discord.ButtonStyle.danger, disabled=not s.running),
            _CardButton("restart", "Restart", "🔄", discord.ButtonStyle.secondary, disabled=not s.running),
            _CardButton("refresh", "Refresh", "🔁", discord.ButtonStyle.secondary),
            _CardButton("pass", "Give a pass", "🎟️", discord.ButtonStyle.primary),
        ))
        self.add_item(card)


def _remember_card(interaction: discord.Interaction) -> None:
    global _card_message
    if interaction.message is not None:
        _card_message = interaction.message


async def update_card() -> None:
    if _card_message is None:
        return
    try:
        await _card_message.edit(view=PersonaCardView())
    except discord.HTTPException as e:
        logger.warning("Could not update the persona card: %s", e)


async def refresh_and_update(force: bool = False) -> None:
    """Re-read the persona bot, redraw the card if anything changed, and tell Direct Replies."""
    global _last_live
    before = _state.signature()
    state = await refresh_state()
    if force or state.signature() != before:
        await update_card()
    live = frozenset(live_guilds(state))
    if live != _last_live:
        _last_live = live
        logger.info("Persona bot live in %s; Direct Replies %s there.", sorted(live) or "no servers",
                    "stand down" if live else "answer")
        try:
            from lib.features.chat_responder import live_chat_manager
            await live_chat_manager.update_dashboard()
        except Exception:
            logger.debug("could not refresh the Direct Replies card", exc_info=True)


async def _poll_loop() -> None:
    while True:
        try:
            await refresh_and_update(force=await expire_passes())
            await sweep_first_messages()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("persona poll failed", exc_info=True)
        settling = time.time() < _settle_until and not (_state.service == "active" and _state.api_up)
        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), 2.0 if settling else POLL_SECS)
        except asyncio.TimeoutError:
            pass


CONTROLLER_ARCHIVE_MINUTES = 10080  # a week, the longest Discord allows


async def keep_controller_open(thread) -> bool:
    """Keep the chatbot controller thread out of the archive, so it stays in people's sidebars.

    Discord archives a thread after a stretch with no new messages, and editing the cards in
    it doesn't count. Returns True when it had to change something."""
    try:
        if not getattr(thread, "archived", False) and \
                getattr(thread, "auto_archive_duration", None) == CONTROLLER_ARCHIVE_MINUTES:
            return False
        await thread.edit(archived=False, auto_archive_duration=CONTROLLER_ARCHIVE_MINUTES,
                          reason="Keep the chatbot controller thread open")
        logger.info("Chatbot controller thread kept open")
        return True
    except discord.HTTPException as e:
        logger.warning("Could not keep the chatbot controller thread open: %s", e)
        return False


async def on_controller_thread_update(client: discord.Client, payload) -> None:
    """Unarchive the controller thread the moment Discord archives it."""
    thread_id = getattr(CHANNELS, "CHATBOT_CONTROLLER_THREAD", 1547254995320184833)
    if payload.thread_id != thread_id:
        return
    meta = (payload.data or {}).get("thread_metadata") or {}
    if not meta.get("archived"):
        return
    thread = payload.thread or client.get_channel(thread_id)
    if thread is None:
        try:
            thread = await client.fetch_channel(thread_id)
        except discord.HTTPException:
            return
    await keep_controller_open(thread)


async def ensure_persona_card(client: discord.Client) -> None:
    """Post or re-attach the card in the chatbot controller thread and start watching the service."""
    global _client, _card_message, _poll_task
    _client = client
    _track_own_sends(client)
    _load_watches()  # pending first-message watches from before a restart
    thread_id = getattr(CHANNELS, "CHATBOT_CONTROLLER_THREAD", 1547254995320184833)
    try:
        await refresh_state()
        thread = client.get_channel(thread_id) or await client.fetch_channel(thread_id)
        await keep_controller_open(thread)
        message = None
        saved = load_json_file(CARD_STATE_FILE).get("message_id")
        if saved:
            try:
                message = await thread.fetch_message(int(saved))
            except discord.HTTPException:
                message = None
        if message is None:
            async for m in thread.history(limit=25):
                if m.author.id == client.user.id and is_persona_card(m):
                    message = m
                    break
        view = PersonaCardView()
        if message is not None:
            await message.edit(view=view)
        else:
            message = await thread.send(view=view, allowed_mentions=discord.AllowedMentions.none())
            logger.info("Posted the persona card (%s) in thread %s", message.id, thread_id)
        atomic_write_json(CARD_STATE_FILE, {"message_id": str(message.id)})
        _card_message = message
    except Exception:
        logger.exception("Could not set up the persona card in thread %s", thread_id)
    if _poll_task is None or _poll_task.done():
        _poll_task = asyncio.create_task(_poll_loop())
