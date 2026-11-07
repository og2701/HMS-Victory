"""Vic's control panel: a thread under #bot-workshop with one Components V2 card per admin
switch, so they don't each need a slash command.

Cards: News feed, Bank, Flags, Visitor overnight mute, UKPence handout and Titles. Each shows
a live status line and the buttons for what its old command (or text trigger) did. Every
button runs the same code the command ran, so there is one code path, not two.

- Who can press: config.CONTROL_PANEL_USER_IDS (Oggers and Hadidas). Anyone else gets a
  private "not for you" and nothing runs. Flows that carry their own checks (the UKP handout
  needs Deputy PM, the title manager needs Oggers or Deputy PM) keep them.
- Every press is logged to #bot-usage-log the way slash commands are, and the presser gets a
  private reply from the action itself.
- The card refreshes after each press, and every CONTROL_PANEL_REFRESH_MINUTES in the
  background, so a change made elsewhere (a poll, a flag) shows up too.
- The buttons are DynamicItems registered in setup_hook, so they keep working after a
  restart. ensure_panel (called from on_ready) creates the thread and cards when they are
  missing, re-posts a deleted card, and keeps the thread from archiving. The ids live in
  config.CONTROL_PANEL_STATE_FILE.

The cards are sent as raw CV2 JSON over REST, like the #bot-workshop task board, which keeps
rendering a pure function that the tests can check without Discord.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta

import discord
import pytz

import config
from config import CHANNELS
from lib.core.file_operations import atomic_write_json, load_json_file

logger = logging.getLogger(__name__)

UK_TZ = pytz.timezone("Europe/London")
CUSTOM_ID_PREFIX = "vcp"
CARD_ORDER = ("newsfeed", "bank", "flags", "overnight", "ukpadd", "titles")
GREEN, ORANGE, RED, GREY, BLUE, GOLD = 0x2ECC71, 0xE67E22, 0xE74C3C, 0x95A5A6, 0x00247D, 0xF1C40F
# The scheduler mutes visitors in #general at 03:00 and lets them back at 07:00 (UK).
MUTE_HOUR, UNMUTE_HOUR = 3, 7

NOT_FOR_YOU = "🎛️ This control panel isn't for you. Only Oggers and Hadidas can use it."
STALE = "🎛️ That button is out of date. The card has been refreshed, so try again."


# ---------------------------------------------------------------- permissions

def allowed_ids() -> set:
    return {int(u) for u in getattr(config, "CONTROL_PANEL_USER_IDS", [])}


def may_use(user_id) -> bool:
    try:
        return int(user_id) in allowed_ids()
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- state

def state_path() -> str:
    return getattr(config, "CONTROL_PANEL_STATE_FILE", os.path.join(config.JSON_DATA_DIR, "control_panel.json"))


def load_state() -> dict:
    try:
        state = load_json_file(state_path()) or {}
    except Exception:
        logger.exception("control panel: could not read %s; starting fresh", state_path())
        state = {}
    state.setdefault("thread_id", None)
    state.setdefault("cards", {})
    return state


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    atomic_write_json(state_path(), state, indent=1)


# ---------------------------------------------------------------- status lines (pure)

def custom_id(card: str, action: str) -> str:
    return f"{CUSTOM_ID_PREFIX}:{card}:{action}"


def _button(card: str, action: str, label: str, emoji: str, style: int = 2) -> dict:
    return {"type": 2, "style": style, "label": label, "emoji": {"name": emoji},
            "custom_id": custom_id(card, action)}


def _uk_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UK_TZ).strftime("%Y-%m-%d")


def newsfeed_card(state: dict, enabled: bool, dry: bool, now: float) -> dict:
    """state is the newsfeed state file; dry is newsfeed.is_dry_run(state)."""
    paused = bool(state.get("paused"))
    if not enabled:
        running, accent = "⚫ **Off** (NEWSFEED_ENABLED is False in config)", GREY
    elif paused:
        running, accent = "⏸️ **Paused**", ORANGE
    else:
        running, accent = "✅ **Running**", GREEN
    mode = "🧪 dry run (decisions go to the log)" if dry else "📣 live"
    day = _uk_day(now)
    posts = (state.get("daily") or {}).get(day, 0)
    updates = (state.get("daily_updates") or {}).get(day, 0)
    last = f"<t:{int(state['last_run'])}:R>" if state.get("last_run") else "never"
    lines = [f"{running} · {mode}",
             f"Last poll {last} · today {posts} posts, {updates} updates"]
    if state.get("last_error"):
        lines.append(f"-# Last error: {str((state['last_error'] or {}).get('error', ''))[:150]}")
    buttons = [
        _button("newsfeed", "resume", "Resume", "▶️", 3) if paused else _button("newsfeed", "pause", "Pause", "⏸️"),
        _button("newsfeed", "run", "Run a poll now", "🔄", 1),
        _button("newsfeed", "live", "Go live", "📣") if dry else _button("newsfeed", "dry-run", "Dry run", "🧪"),
        _button("newsfeed", "status", "Full status", "📋"),
    ]
    return {"title": "## 📰 News feed", "sub": "The BBC news forum auto-poster (was /newsfeed)",
            "status": "\n".join(lines), "accent": accent, "buttons": buttons}


def bank_card(info: dict, reserve: dict) -> dict:
    lines = [f"💰 **{int(info.get('balance', 0)):,}** UKP in the bank · "
             f"{int(info.get('total_revenue', 0)):,} revenue · {int(info.get('total_tax_collected', 0) or 0):,} tax"]
    accent = GREEN
    if reserve:
        lines.append(f"Reserves {int(reserve.get('reserves', 0)):,} ({reserve.get('pct_of_supply', 0.0):.1f}% of supply)")
        if reserve.get("throttled"):
            accent = ORANGE
            lines.append(f"🚦 **Throttle on:** rewards paying {reserve.get('multiplier', 1.0):.0%}")
    return {"title": "## 🏦 Bank", "sub": "The server bank (was /bank-status)",
            "status": "\n".join(lines), "accent": accent,
            "buttons": [_button("bank", "report", "Full report", "📊", 1)]}


def flags_card(rows: list, tier_label=None) -> dict:
    """rows from restrictions.restricted_members(): (user_id, tier, ts, note)."""
    tier_label = tier_label or (lambda t: t)
    if not rows:
        status, accent = "✅ Nobody is restricted", GREEN
    else:
        counts = {}
        for _uid, tier, _ts, _note in rows:
            counts[tier] = counts.get(tier, 0) + 1
        split = ", ".join(f"{n} {tier_label(t)}" for t, n in sorted(counts.items()))
        who = "member is" if len(rows) == 1 else "members are"
        status, accent = f"🚫 **{len(rows)}** {who} restricted ({split})", ORANGE
        newest = rows[0]
        status += f"\n-# Newest: <@{newest[0]}> <t:{int(newest[2])}:R>"
    return {"title": "## 🛡️ Flags", "sub": "Anti-cheat flags and economy restrictions (was /flags)",
            "status": status, "accent": accent,
            "buttons": [_button("flags", "review", "Review flags", "🔎", 1)]}


def next_uk_time(now: float, hour: int) -> int:
    """The next hour:00 UK time after now, as a unix timestamp (DST-aware)."""
    local = datetime.fromtimestamp(now, UK_TZ)
    day = local.date()
    for _ in range(3):
        target = UK_TZ.localize(datetime(day.year, day.month, day.day, hour))
        if target.timestamp() > now:
            return int(target.timestamp())
        day += timedelta(days=1)
    return int(now)


def overnight_card(active: bool, now: float) -> dict:
    if active:
        status = (f"🌙 **On** · visitors are muted in <#{CHANNELS.GENERAL}> from 03:00 to 07:00 UK\n"
                  f"Next mute <t:{next_uk_time(now, MUTE_HOUR)}:R>, next unmute <t:{next_uk_time(now, UNMUTE_HOUR)}:R>")
        button, accent = _button("overnight", "off", "Turn off", "☀️", 4), GREEN
    else:
        status = "☀️ **Off** · visitors can talk overnight"
        button, accent = _button("overnight", "on", "Turn on", "🌙", 3), GREY
    return {"title": "## 🌙 Visitor overnight mute", "sub": "Was /toggle-visitor-overnight-mute",
            "status": status, "accent": accent, "buttons": [button]}


def ukpadd_card(balance: int) -> dict:
    return {"title": "## 🪙 UKPence handout", "sub": "Pay people or a whole role from the bank (same as `ukpadd`)",
            "status": f"💰 **{int(balance):,}** UKP available in the bank\n-# The handout itself still needs the Deputy PM role.",
            "accent": GOLD, "buttons": [_button("ukpadd", "open", "Hand out UKPence", "🪙", 1)]}


def titles_card(held: int, total: int) -> dict:
    return {"title": "## 🎖️ Titles", "sub": "Give or clear a member's title (same as `titleadd`)",
            "status": f"🎖️ **{int(held)}** of {int(total)} titles held",
            "accent": BLUE, "buttons": [_button("titles", "open", "Open title manager", "🎖️", 1)]}


def render(card: dict, now: float = None) -> list:
    now = time.time() if now is None else now
    items = [
        {"type": 10, "content": f"{card['title']}\n-# {card['sub']}"},
        {"type": 14, "divider": True, "spacing": 1},
        {"type": 10, "content": card["status"]},
        {"type": 1, "components": card["buttons"][:5]},
        {"type": 10, "content": f"-# Updated <t:{int(now)}:R> · Oggers and Hadidas only"},
    ]
    return [{"type": 17, "accent_color": card["accent"], "components": items}]


def payload(card: dict, now: float = None) -> dict:
    return {"flags": 1 << 15, "components": render(card, now), "allowed_mentions": {"parse": []}}


def header_payload() -> dict:
    who = " and ".join(f"<@{u}>" for u in getattr(config, "CONTROL_PANEL_USER_IDS", []))
    text = ("## 🎛️ Vic's control panel\n"
            "Admin switches that used to be slash commands. Each card shows what's on now and has the buttons. "
            f"Only {who} can press them; replies are private and every press goes in <#{CHANNELS.BOT_USAGE_LOG}>.")
    return {"flags": 1 << 15, "allowed_mentions": {"parse": []},
            "components": [{"type": 17, "accent_color": BLUE, "components": [{"type": 10, "content": text}]}]}


# ---------------------------------------------------------------- live data

def gather(key: str, now: float = None) -> dict:
    """The card for key, built from live data. A read that fails shows on the card instead."""
    now = time.time() if now is None else now
    try:
        if key == "newsfeed":
            from lib.features import newsfeed
            state = newsfeed.load_state()
            return newsfeed_card(state, bool(config.NEWSFEED_ENABLED), newsfeed.is_dry_run(state), now)
        if key == "bank":
            from lib.economy.bank_manager import BankManager
            try:
                from lib.economy.reserve_policy import reserve_state
                reserve = reserve_state() or {}
            except Exception:
                reserve = {}
            return bank_card(BankManager.get_bank_info(), reserve)
        if key == "flags":
            from lib.core import restrictions as R
            return flags_card(R.restricted_members(), R.tier_label)
        if key == "overnight":
            return overnight_card(os.path.exists(config.OVERNIGHT_MUTE_FILE), now)
        if key == "ukpadd":
            from lib.economy.bank_manager import BankManager
            return ukpadd_card(BankManager.get_balance())
        if key == "titles":
            from database import DatabaseManager
            from lib.features.titles import TITLES
            row = DatabaseManager.fetch_one(
                "SELECT COUNT(*) FROM user_rank_customization WHERE title IS NOT NULL")
            return titles_card(int(row[0]) if row else 0, len(TITLES))
    except Exception as e:
        logger.exception("control panel: could not read status for %s", key)
        card = _fallback(key)
        card["status"] = f"⚠️ Couldn't read the status: {type(e).__name__}"
        card["accent"] = RED
        return card
    raise KeyError(key)


def _fallback(key: str) -> dict:
    """The card's buttons with no live data, so a broken read never strands the actions."""
    now = time.time()
    return {
        "newsfeed": lambda: newsfeed_card({}, True, False, now),
        "bank": lambda: bank_card({}, {}),
        "flags": lambda: flags_card([]),
        "overnight": lambda: overnight_card(False, now),
        "ukpadd": lambda: ukpadd_card(0),
        "titles": lambda: titles_card(0, 0),
    }[key]()


# ---------------------------------------------------------------- actions

async def _newsfeed(interaction, action):
    from lib.features.newsfeed import handle_newsfeed_command
    await handle_newsfeed_command(interaction, action)


async def _bank_report(interaction, action):
    from commands.economy.bank_commands import handle_bank_status_command
    await handle_bank_status_command(interaction)


async def _flags_review(interaction, action):
    from lib.core import detection
    content, view = detection.build_flags_panel(interaction.client, interaction.user.id)
    await interaction.response.send_message(content, view=view, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


async def _overnight(interaction, action):
    from commands.moderation.overnight_mute import toggle_overnight_mute
    want = action == "on"
    if os.path.exists(config.OVERNIGHT_MUTE_FILE) == want:
        await interaction.response.send_message(
            f"Overnight mute for visitors is already {'on' if want else 'off'}.", ephemeral=True)
        return
    await toggle_overnight_mute(interaction)


async def _ukpadd(interaction, action):
    # The same picker the `ukpadd` text trigger opens, for the presser alone. It keeps its own
    # Deputy PM check (and the bank's logging), so it opens only for someone who could use it.
    from lib.economy.bank_commands_ui import UKPAddPickerView, _is_deputy_pm
    if not _is_deputy_pm(interaction.user):
        await interaction.response.send_message("❌ Only the Deputy PM can hand out UKPence.", ephemeral=True)
        return
    await interaction.response.send_message(view=UKPAddPickerView(interaction.user.id), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


async def _titles(interaction, action):
    # The `titleadd` launcher's own button callback, which checks Oggers or Deputy PM and then
    # opens the member picker privately.
    from lib.features.titles import TitleLaunchView
    view = TitleLaunchView()
    await view.open_manager.callback(interaction)


ACTIONS = {
    "newsfeed": {"pause": _newsfeed, "resume": _newsfeed, "run": _newsfeed,
                 "dry-run": _newsfeed, "live": _newsfeed, "status": _newsfeed},
    "bank": {"report": _bank_report},
    "flags": {"review": _flags_review},
    "overnight": {"on": _overnight, "off": _overnight},
    "ukpadd": {"open": _ukpadd},
    "titles": {"open": _titles},
}
# Actions that only open something: the status can't have changed, so no refresh.
READ_ONLY = {("newsfeed", "status"), ("bank", "report"), ("ukpadd", "open"), ("titles", "open")}


def usage_line(user, card: str, action: str, now: datetime = None) -> str:
    now = now or datetime.now(UK_TZ)
    return (f"{now.strftime('%Y-%m-%d %H:%M:%S')} - {user} (ID {user.id}) pressed "
            f"{card} › {action} on the control panel")


async def _log(client, text: str) -> None:
    try:
        channel = client.get_channel(CHANNELS.BOT_USAGE_LOG)
        if channel is not None:
            await channel.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.debug("control panel: could not log a press", exc_info=True)


async def dispatch(interaction, card: str, action: str) -> str:
    """Handle one press. Returns what happened, for the tests: refused, stale, done or failed."""
    if not may_use(interaction.user.id):
        await interaction.response.send_message(NOT_FOR_YOU, ephemeral=True)
        asyncio.create_task(_log(interaction.client, usage_line(interaction.user, card, action) + " (refused)"))
        return "refused"
    handler = ACTIONS.get(card, {}).get(action)
    if handler is None:
        await interaction.response.send_message(STALE, ephemeral=True)
        if card in ACTIONS:
            asyncio.create_task(refresh_card(interaction.client, card))
        return "stale"
    asyncio.create_task(_log(interaction.client, usage_line(interaction.user, card, action)))
    result = "done"
    try:
        await handler(interaction, action)
    except Exception:
        logger.exception("control panel: %s/%s failed", card, action)
        result = "failed"
        msg = "⚠️ That didn't work. It's in the bot's logs."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass
    if (card, action) not in READ_ONLY:
        await refresh_card(interaction.client, card)
    return result


class PanelButton(discord.ui.DynamicItem[discord.ui.Button],
                  template=r"vcp:(?P<card>[a-z]+):(?P<action>[a-z-]+)"):
    def __init__(self, card: str = "bank", action: str = "report"):
        self.card, self.action = card, action
        super().__init__(discord.ui.Button(label=action, custom_id=custom_id(card, action)))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["card"], match["action"])

    async def callback(self, interaction):
        await dispatch(interaction, self.card, self.action)


BUTTONS = (PanelButton,)


# ---------------------------------------------------------------- posting

_lock = asyncio.Lock()
_loop_task = None


def _route(method: str, path: str, **params):
    from discord.http import Route
    return Route(method, path, **params)


async def _post(client, channel_id: int, body: dict) -> int:
    data = await client.http.request(_route("POST", "/channels/{channel_id}/messages", channel_id=int(channel_id)),
                                     json=body)
    return int(data["id"])


async def _patch(client, channel_id: int, message_id: int, body: dict) -> None:
    await client.http.request(_route("PATCH", "/channels/{channel_id}/messages/{message_id}",
                                     channel_id=int(channel_id), message_id=int(message_id)), json=body)


async def _thread(client, state: dict):
    """The panel thread, unarchived; a new one if it's gone. Returns (thread, created)."""
    thread = None
    if state.get("thread_id"):
        try:
            thread = client.get_channel(int(state["thread_id"])) or await client.fetch_channel(int(state["thread_id"]))
        except discord.NotFound:
            thread = None
        if thread is not None and getattr(thread, "parent_id", None) != CHANNELS.BOT_WORKSHOP:
            thread = None
    if thread is None:
        parent = client.get_channel(CHANNELS.BOT_WORKSHOP) or await client.fetch_channel(CHANNELS.BOT_WORKSHOP)
        thread = await parent.create_thread(name=config.CONTROL_PANEL_THREAD_NAME,
                                            type=discord.ChannelType.public_thread,
                                            auto_archive_duration=10080,
                                            reason="Vic's control panel")
        logger.info("control panel: created thread %s", thread.id)
        return thread, True
    if getattr(thread, "archived", False):
        await thread.edit(archived=False)
    return thread, False


async def _sync(client, keys=None, header: bool = False) -> None:
    """Redraw the cards from live status, posting any that are missing or were deleted.
    header=True also redraws (or re-posts) the intro card at the top."""
    state = load_state()
    thread, created = await _thread(client, state)
    if created or str(thread.id) != str(state.get("thread_id")):
        state = {"thread_id": str(thread.id), "cards": {}}
    changed = False
    header_id = state["cards"].get("header")
    if header_id and header:
        try:
            await _patch(client, thread.id, int(header_id), header_payload())
        except discord.NotFound:
            header_id = None
    if not header_id:
        state["cards"]["header"] = str(await _post(client, thread.id, header_payload()))
        changed = True
    for key in (keys or CARD_ORDER):
        card = await asyncio.to_thread(gather, key)
        body = payload(card)
        message_id = state["cards"].get(key)
        if message_id:
            try:
                await _patch(client, thread.id, int(message_id), body)
                continue
            except discord.NotFound:
                logger.info("control panel: %s card was deleted, re-posting", key)
        state["cards"][key] = str(await _post(client, thread.id, body))
        changed = True
    if changed:
        save_state(state)


async def refresh_card(client, key: str) -> None:
    try:
        async with _lock:
            await _sync(client, [key])
    except Exception:
        logger.warning("control panel: could not refresh %s", key, exc_info=True)


async def _refresh_loop(client) -> None:
    minutes = max(1, int(getattr(config, "CONTROL_PANEL_REFRESH_MINUTES", 5)))
    while True:
        await asyncio.sleep(minutes * 60)
        try:
            async with _lock:
                await _sync(client)
        except Exception:
            logger.warning("control panel: background refresh failed", exc_info=True)


async def ensure_panel(client) -> None:
    """Create the thread and cards if they're missing, redraw them all, and start the
    background refresh. Safe to call on every on_ready."""
    global _loop_task
    try:
        async with _lock:
            await _sync(client, header=True)
    except Exception:
        logger.exception("control panel: could not set up the panel")
    if _loop_task is None or _loop_task.done():
        _loop_task = asyncio.create_task(_refresh_loop(client))
