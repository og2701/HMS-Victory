"""Spayed: bot-spam taken off people who only come to the server to farm BallsDex.

BallsDex spawns a ball in #bot-spam every quarter of an hour or so, round the clock, and
catching one is a button and a name typed into a pop-up, with no chatting needed at all.
Over the 90 days to October 2026, 55 of the 87 people who caught one had posted five
messages or fewer anywhere else, and they took two thirds of the catches. This finds them
and gives them the Spayed role, which hides bot-spam.

Everything is read back out of the chronicle, which has kept every BallsDex message since
2023, so there is nothing new to record and the first run already has a full window. A
catch is a BallsDex message carrying the ball's stat line, `(#76BE560, -10%/+0%)`, and the
catcher is the first person it mentions. "Caught already" and "too slow" replies carry no
stat line, so failed attempts don't count.

Messages elsewhere are counted the way XP is (one every two minutes, at least two
characters or some media, nothing in bot-spam or its threads), so a burst of filler in
#general barely moves the number.

Spaying happens the moment BallsDex posts the catch that takes someone over the line
(on_catch). The three-hourly sweep does the rest: lifting it from people who have started
chatting, putting it back after a rejoin the bot missed, and anyone a restart skipped.

ball_spays holds everyone wearing the role, whoever put it on, so leaving and rejoining
brings it straight back. Only 'auto' rows are ever lifted by the bot. Staff taking an auto
spay off by hand counts as a pardon, the same as the button, so the next sweep doesn't put
it back.
"""

import asyncio
import logging
import re
import sqlite3
import time
from collections import Counter

import discord

import config
from config import CHANNELS, CHRONICLE_DB_FILE, GUILD_ID, ROLES, USERS
from database import DatabaseManager

logger = logging.getLogger(__name__)

STAFF_ROLE_IDS = {ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE}
XP_COOLDOWN = 120          # seconds, the same gap xp_system wants between counted messages
CARD_LIMIT = 20            # members per police station card (buttons, five to a row)
NO_PINGS = discord.AllowedMentions.none()

_STAT_LINE = re.compile(r"`\(#[0-9A-Fa-f]+, [+-]?\d+%/[+-]?\d+%\)`")
_MENTION = re.compile(r"<@!?(\d+)>")
_sweep_lock = asyncio.Lock()


def _cfg(name, default):
    return getattr(config, name, default)


# ------------------------------------------------------------------ reading the chronicle

def catcher_of(content):
    """The member a BallsDex message says caught a ball, or None if it isn't a catch."""
    if not content or not _STAT_LINE.search(content):
        return None
    m = _MENTION.search(content)
    return int(m.group(1)) if m else None


def count_contribution(rows):
    """(counted messages, distinct UK days) from one member's chronicle rows, oldest first.

    rows: (ts, local_day, msg_type, content, n_attachments, stickers, channel_id, parent_id)
    """
    counted, days, last = 0, set(), None
    for ts, day, msg_type, content, n_att, stickers, channel_id, parent_id in rows:
        if CHANNELS.BOT_SPAM in (channel_id, parent_id):
            continue
        # NULL is a row from before msg_type was recorded: an ordinary message.
        if msg_type not in (None, "default", "reply"):
            continue
        if len((content or "").strip()) < 2 and not n_att and stickers in (None, "", "[]"):
            continue
        if last is not None and ts - last < XP_COOLDOWN:
            continue
        last = ts
        counted += 1
        days.add(day)
    return counted, len(days)


def _connect(path=None):
    # Its own read-only connection, not ChronicleDB's: that one is shared with the recorder
    # behind a lock, and every on_message would sit waiting on this read.
    return sqlite3.connect(f"file:{path or CHRONICLE_DB_FILE}?mode=ro", uri=True, timeout=10)


def catches_since(conn, since):
    counts = Counter()
    for (content,) in conn.execute(
            "SELECT content FROM messages WHERE user_id = ? AND ts >= ?",
            (USERS.COUNTRYBALL_BOT, since)):
        uid = catcher_of(content)
        if uid:
            counts[uid] += 1
    return counts


def catches_by(conn, user_id, since, skip_message_id=None):
    """One member's catches since ``since``, leaving out ``skip_message_id``."""
    n = 0
    for (content,) in conn.execute(
            "SELECT content FROM messages WHERE user_id = ? AND ts >= ? AND message_id != ? "
            "AND content LIKE ?",
            (USERS.COUNTRYBALL_BOT, since, skip_message_id or 0, f"%{user_id}>%")):
        if catcher_of(content) == user_id:
            n += 1
    return n


def contribution_since(conn, user_id, since):
    rows = conn.execute(
        "SELECT ts, local_day, msg_type, content, n_attachments, stickers, channel_id, parent_id "
        "FROM messages WHERE user_id = ? AND ts >= ? AND is_bot = 0 AND deleted_ts IS NULL "
        "ORDER BY ts", (user_id, since))
    return count_contribution(rows)


def gather(now, lift_from, path=None):
    """Everything a sweep needs from the chronicle, read in one go off the event loop.

    lift_from is {user_id: spayed_ts} for the auto spays that could be due to come off.
    Returns (catches in the window, (messages, days) in the window for everyone over the
    catch line, (messages, days) since the spay for each of lift_from).
    """
    since = now - _cfg("BALL_SPAY_WINDOW_DAYS", 30) * 86400
    floor = _cfg("BALL_SPAY_MIN_CATCHES", 20)
    conn = _connect(path)
    try:
        catches = catches_since(conn, since)
        recent = {uid: contribution_since(conn, uid, since)
                  for uid, n in catches.items() if n >= floor}
        lifts = {uid: contribution_since(conn, uid, ts) for uid, ts in lift_from.items()}
    finally:
        conn.close()
    return catches, recent, lifts


def check_one(user_id, now, catch_message_id, path=None):
    """(catches, messages) in the window for one catcher, counting the catch just posted.

    That catch is left out of the read and added back on, because the recorder queues
    its writes and it may not be on disk yet; this way it counts once either way.
    """
    since = now - _cfg("BALL_SPAY_WINDOW_DAYS", 30) * 86400
    conn = _connect(path)
    try:
        n = catches_by(conn, user_id, since, catch_message_id) + 1
        msgs, _ = contribution_since(conn, user_id, since)
    finally:
        conn.close()
    return n, msgs


def pick_spays(catches, recent, eligible):
    """[(user_id, catches, messages)] for everyone over the line, most catches first.

    eligible is the ids that may be spayed at all: in the server, not staff, not boosting,
    not pardoned and not spayed already. The caller decides that, since it has the guild.
    """
    floor = _cfg("BALL_SPAY_MIN_CATCHES", 20)
    cap = _cfg("BALL_SPAY_MAX_MESSAGES", 10)
    out = []
    for uid, n in catches.most_common():
        if n < floor:
            break
        msgs = recent.get(uid, (0, 0))[0]
        if uid in eligible and msgs < cap:
            out.append((uid, n, msgs))
    return out


def due_lift(messages, days):
    return (messages >= _cfg("BALL_SPAY_LIFT_MESSAGES", 30)
            and days >= _cfg("BALL_SPAY_LIFT_DAYS", 5))


# ------------------------------------------------------------------------------ state

def _rows():
    return {int(uid): (source, ts) for uid, source, ts in
            DatabaseManager.fetch_all("SELECT user_id, source, ts FROM ball_spays") or []}


def _row(user_id):
    return DatabaseManager.fetch_one(
        "SELECT source, ts FROM ball_spays WHERE user_id = ?", (str(user_id),))


def _record(user_id, source, ts, catches=None, messages=None):
    DatabaseManager.execute(
        "INSERT OR REPLACE INTO ball_spays (user_id, source, ts, catches, messages) "
        "VALUES (?, ?, ?, ?, ?)", (str(user_id), source, ts, catches, messages))


def _forget(user_id):
    DatabaseManager.execute("DELETE FROM ball_spays WHERE user_id = ?", (str(user_id),))


def _pardoned():
    return {int(uid) for (uid,) in
            DatabaseManager.fetch_all("SELECT user_id FROM ball_spay_pardons") or []}


def pardon(user_id, by_id=None):
    """Never spay this member automatically again. Forgets the spay row too."""
    _forget(user_id)
    DatabaseManager.execute(
        "INSERT OR REPLACE INTO ball_spay_pardons (user_id, ts, by_id) VALUES (?, ?, ?)",
        (str(user_id), int(time.time()), str(by_id) if by_id else None))


def _exempt(member):
    return (member.bot or member.premium_since is not None
            or any(r.id in STAFF_ROLE_IDS for r in member.roles))


# ------------------------------------------------------------------------------ roles

async def _add_role(member, role, reason):
    try:
        await member.add_roles(role, reason=reason)
        return True
    except discord.HTTPException:
        logger.warning("Spayed: could not add the role to %s", member.id, exc_info=True)
        return False


async def _remove_role(member, role, reason):
    try:
        await member.remove_roles(role, reason=reason)
        return True
    except discord.HTTPException:
        logger.warning("Spayed: could not remove the role from %s", member.id, exc_info=True)
        return False


# ------------------------------------------------------------------------------ sweep

async def sweep(client):
    """Spay the farmers, lift the reformed, and keep ball_spays in step with the role."""
    if not _cfg("BALL_SPAY_ENABLED", False):
        return
    async with _sweep_lock:
        try:
            await _sweep(client)
        except Exception:
            logger.error("Spayed sweep failed", exc_info=True)


async def _sweep(client):
    guild = client.get_guild(GUILD_ID)
    role = guild.get_role(ROLES.SPAYED) if guild else None
    if role is None:
        logger.warning("Spayed sweep: no guild or no Spayed role")
        return
    live = _cfg("BALL_SPAY_LIVE", False)
    window = _cfg("BALL_SPAY_WINDOW_DAYS", 30)
    now = int(time.time())
    rows = _rows()

    # Wearing the role with no row means it went on by hand, or before this existed.
    for member in role.members:
        if member.id not in rows:
            _record(member.id, "staff", now)
            rows[member.id] = ("staff", now)
    # A row and no role means they rejoined while the bot was down to see it.
    for uid in rows:
        member = guild.get_member(uid)
        if member is not None and role not in member.roles:
            await _add_role(member, role, "Spayed: back in the server")

    auto = {uid: ts for uid, (source, ts) in rows.items()
            if source == "auto" and guild.get_member(uid) is not None}
    catches, recent, since_spay = await asyncio.to_thread(gather, now, auto)

    pardoned = _pardoned()
    eligible = set()
    for uid in recent:
        member = guild.get_member(uid)
        if member and uid not in rows and uid not in pardoned and not _exempt(member):
            eligible.add(uid)

    spayed, lifted = [], []
    for uid, n, msgs in pick_spays(catches, recent, eligible):
        member = guild.get_member(uid)
        if live:
            _record(uid, "auto", now, n, msgs)
            if not await _add_role(member, role, f"Spayed: {n} ball catches and {msgs} "
                                                 f"messages elsewhere in {window} days"):
                _forget(uid)
                continue
        spayed.append((member, n, msgs))
    for uid, ts in auto.items():
        msgs, days = since_spay[uid]
        if not due_lift(msgs, days):
            continue
        member = guild.get_member(uid)
        if live:
            # The row goes first, so on_member_update knows this wasn't staff.
            _forget(uid)
            if not await _remove_role(member, role, "Spayed lifted: chatting in the server"):
                _record(uid, "auto", ts)
                continue
        lifted.append((member, msgs, days, ts))

    if spayed or lifted:
        logger.info("Spayed sweep: %d spayed, %d lifted (live=%s)", len(spayed), len(lifted), live)
    await _report(client, spayed, lifted, live)


async def on_catch(client, message):
    """Spay a catcher the moment the catch that takes them over the line is posted."""
    # Shadow mode leaves this to the sweep, or it would post "would spay" on every catch.
    if not _cfg("BALL_SPAY_ENABLED", False) or not _cfg("BALL_SPAY_LIVE", False):
        return
    uid = catcher_of(message.content)
    guild = message.guild
    if uid is None or guild is None or guild.id != GUILD_ID:
        return
    role = guild.get_role(ROLES.SPAYED)
    member = guild.get_member(uid)
    if role is None or member is None or role in member.roles or _exempt(member):
        return
    try:
        async with _sweep_lock:
            if _row(uid) or uid in _pardoned():
                return
            now = int(time.time())
            n, msgs = await asyncio.to_thread(check_one, uid, now, message.id)
            if n < _cfg("BALL_SPAY_MIN_CATCHES", 20) or msgs >= _cfg("BALL_SPAY_MAX_MESSAGES", 10):
                return
            window = _cfg("BALL_SPAY_WINDOW_DAYS", 30)
            _record(uid, "auto", now, n, msgs)
            if not await _add_role(member, role, f"Spayed: {n} ball catches and {msgs} "
                                                 f"messages elsewhere in {window} days"):
                _forget(uid)
                return
        logger.info("Spayed %s on catch %d (%d messages elsewhere)", uid, n, msgs)
        await _report(client, [(member, n, msgs)], [], True)
    except Exception:
        logger.error("Spayed: catch check failed for %s", uid, exc_info=True)


# ----------------------------------------------------------------- police station cards

def _name(member):
    return discord.utils.escape_markdown(member.display_name)


def _plural(n, one, many=None):
    return f"**{n:,}** {one if n == 1 else (many or one + 's')}"


def spay_card(spays, live=True):
    """One police station card for everyone spayed in a sweep, a Pardon each."""
    window = _cfg("BALL_SPAY_WINDOW_DAYS", 30)
    floor = _cfg("BALL_SPAY_MIN_CATCHES", 20)
    cap = _cfg("BALL_SPAY_MAX_MESSAGES", 10)
    if len(spays) == 1:
        who = _name(spays[0][0])
        head = f"### ✂️ {who} has been spayed" if live else f"### ✂️ Would spay {who}"
    else:
        head = (f"### ✂️ {len(spays)} members have been spayed" if live
                else f"### ✂️ Would spay {len(spays)} members")
    lines = [f"{m.mention} · {_plural(n, 'catch', 'catches')}, "
             f"{_plural(msgs, 'message')} outside bot-spam in {window} days"
             for m, n, msgs in spays]
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0xE67E22)
    card.add_item(discord.ui.TextDisplay(head + "\n" + "\n".join(lines)))
    card.add_item(discord.ui.Separator())
    if live:
        buttons = [PardonButton(m.id, f"Pardon {m.display_name}") for m, _, _ in spays]
        for i in range(0, len(buttons), 5):
            card.add_item(discord.ui.ActionRow(*buttons[i:i + 5]))
    card.add_item(discord.ui.TextDisplay(
        f"-# Ball farming: {floor}+ catches and under {cap} messages anywhere else in "
        f"{window} days. It comes off by itself once they chat, and Pardon takes it off "
        f"and stops it coming back."))
    view.add_item(card)
    return view


def lift_card(lifts, live=True):
    view = discord.ui.LayoutView(timeout=None)
    card = discord.ui.Container(accent_colour=0x2ECC71)
    head = "### 🐾 Unspayed" if live else "### 🐾 Would unspay"
    lines = [f"{m.mention} · {_plural(msgs, 'message')} on {days} days since being spayed "
             f"<t:{ts}:R>" for m, msgs, days, ts in lifts]
    card.add_item(discord.ui.TextDisplay(head + "\n" + "\n".join(lines)))
    view.add_item(card)
    return view


async def _report(client, spayed, lifted, live):
    if not (spayed or lifted):
        return
    channel = client.get_channel(CHANNELS.POLICE_STATION)
    if channel is None:
        logger.warning("Spayed: no police station channel to report to")
        return
    try:
        for i in range(0, len(spayed), CARD_LIMIT):
            await channel.send(view=spay_card(spayed[i:i + CARD_LIMIT], live),
                               allowed_mentions=NO_PINGS)
        for i in range(0, len(lifted), CARD_LIMIT):
            await channel.send(view=lift_card(lifted[i:i + CARD_LIMIT], live),
                               allowed_mentions=NO_PINGS)
    except discord.HTTPException:
        logger.error("Spayed: could not post to the police station", exc_info=True)


class PardonButton(discord.ui.DynamicItem[discord.ui.Button], template=r"spay:pardon:(?P<uid>\d+)"):
    """Staff-only: take the role off and never spay this member automatically again."""

    def __init__(self, user_id, label="Pardon"):
        self.user_id = int(user_id)
        super().__init__(discord.ui.Button(
            label=label[:80], emoji="🔓", style=discord.ButtonStyle.secondary,
            custom_id=f"spay:pardon:{self.user_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["uid"]), getattr(item, "label", None) or "Pardon")

    async def callback(self, interaction):
        if not any(getattr(r, "id", None) in STAFF_ROLE_IDS
                   for r in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("Only staff can pardon.", ephemeral=True)
            return
        pardon(self.user_id, interaction.user.id)
        guild = interaction.guild
        member = guild.get_member(self.user_id) if guild else None
        role = guild.get_role(ROLES.SPAYED) if guild else None
        if member is not None and role is not None and role in member.roles:
            await _remove_role(member, role, f"Spayed: pardoned by {interaction.user}")

        view = None
        if interaction.message is not None:
            try:
                view = discord.ui.LayoutView.from_message(interaction.message)
                for child in view.walk_children():
                    if isinstance(child, discord.ui.Button) and child.custom_id == self.custom_id:
                        child.disabled = True
                        child.style = discord.ButtonStyle.success
                        child.label = (child.label or "Pardon").replace("Pardon", "Pardoned", 1)[:80]
            except Exception:
                logger.debug("Spayed: could not rebuild the card to grey out Pardon", exc_info=True)
                view = None
        if view is not None:
            await interaction.response.edit_message(view=view)
            await interaction.followup.send(
                f"🔓 {interaction.user.mention} pardoned <@{self.user_id}>, who won't be "
                f"spayed automatically again.", allowed_mentions=NO_PINGS)
        else:
            await interaction.response.send_message(
                f"🔓 {interaction.user.mention} pardoned <@{self.user_id}>, who won't be "
                f"spayed automatically again.", allowed_mentions=NO_PINGS)


# ------------------------------------------------------------------------------ hooks

async def on_member_join(member):
    """Put the role straight back on anyone who left while spayed."""
    if not _cfg("BALL_SPAY_ENABLED", False) or member.guild.id != GUILD_ID:
        return
    if not _row(member.id):
        return
    role = member.guild.get_role(ROLES.SPAYED)
    if role is not None and role not in member.roles:
        await _add_role(member, role, "Spayed: rejoined while spayed")


async def on_member_update(before, after):
    """Keep ball_spays in step when staff put the role on or take it off by hand.

    The bot writes or deletes its own row before it touches the role, so a change with
    the row already in the right state was the bot's, and anything else was staff.
    """
    if not _cfg("BALL_SPAY_ENABLED", False) or after.guild.id != GUILD_ID:
        return
    had = any(r.id == ROLES.SPAYED for r in before.roles)
    has = any(r.id == ROLES.SPAYED for r in after.roles)
    if had == has:
        return
    row = _row(after.id)
    if has and not row:
        _record(after.id, "staff", int(time.time()))
    elif had and row:
        _forget(after.id)
        if row[0] != "auto":
            return
        pardon(after.id)
        channel = after._state._get_client().get_channel(CHANNELS.POLICE_STATION)
        if channel is not None:
            try:
                await channel.send(
                    f"🔓 {after.mention}'s spay was taken off by hand, so it counts as a "
                    f"pardon: they won't be spayed automatically again.",
                    allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                logger.debug("Spayed: could not post the hand-lift note", exc_info=True)
