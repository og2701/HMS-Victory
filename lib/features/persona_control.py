"""Discord control card for the persona bot: the full Vic with memory and the persona.

The persona bot is its own service on this box (systemd unit ``persona-bot``) and
logs in with the same token as this bot. Its card sits under the Direct Replies
card in the chatbot controller thread. It starts, stops and restarts the service,
and sets each channel's mode through the console API on 127.0.0.1:8765, which is
the same API the Tailscale console uses, so both always show the same Vic.

While the persona bot is on in a server, Direct Replies stand down there, so a
tag never gets two answers from two different Vics.
"""

import asyncio
import logging
import os
import time
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
SLEEP_CHOICES = ((30, "30 min"), (60, "1 hour"), (120, "2 hours"), (240, "4 hours"))


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
        card.add_item(discord.ui.Separator(visible=True))
        card.add_item(discord.ui.ActionRow(
            _CardButton("start", "Start", "▶️", discord.ButtonStyle.success, disabled=s.running),
            _CardButton("stop", "Stop", "⏹️", discord.ButtonStyle.danger, disabled=not s.running),
            _CardButton("restart", "Restart", "🔄", discord.ButtonStyle.secondary, disabled=not s.running),
            _CardButton("refresh", "Refresh", "🔁", discord.ButtonStyle.secondary),
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
            await refresh_and_update()
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


async def ensure_persona_card(client: discord.Client) -> None:
    """Post or re-attach the card in the chatbot controller thread and start watching the service."""
    global _client, _card_message, _poll_task
    _client = client
    thread_id = getattr(CHANNELS, "CHATBOT_CONTROLLER_THREAD", 1547254995320184833)
    try:
        await refresh_state()
        thread = client.get_channel(thread_id) or await client.fetch_channel(thread_id)
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
