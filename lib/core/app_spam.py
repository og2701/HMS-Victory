"""Stops apps that people install on their own account from flooding chat.

Since Discord added user-installed apps, anyone can bring an app into any server they are
in and have it post there. The app was never invited, so it is not a member and no member
check sees it, and the messages are not the person's own, so nothing that watches members
sees them either. On 16 Feb 2025 one of these ("Spectre") flooded #general with an invite
link: kicking the account "didn't do much", because an app can keep posting follow-ups on
one command for fifteen minutes, and the only fix was turning Use External Apps off.

So this watches the messages themselves. A message counts when its interaction_metadata
says it came from an app installed to the person who ran it, and not to the server.
Every bot the server invited is ignored, and so are the apps on the allowlist.

Two rules, both checked as each message arrives:

- **one person:** APP_SPAM_USER_COUNT messages from their apps inside
  APP_SPAM_USER_WINDOW_SECONDS.
- **a raid:** APP_SPAM_RAID_COUNT messages from one app, from at least
  APP_SPAM_RAID_MIN_USERS people, inside APP_SPAM_RAID_WINDOW_SECONDS. Several accounts each
  posting a couple of times never trip the first rule, so this one acts too.

When either fires, the burst is deleted, everyone in it gets a short timeout (which takes
Use External Apps away with everything else, and lifts itself), and anything more from
their apps is deleted for APP_SPAM_CLEANUP_SECONDS, the life of an interaction token. A
raid also marks the app: anyone else who uses it while the mark lasts is blocked on their
first message. Each person blocked gets a card in police station with Remove timeout first,
since a false positive is the likeliest thing a human has to put right.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import discord

import config
from config import CHANNELS, USERS

log = logging.getLogger(__name__)

MAX_CARDS = 10           # one card per person; past this, one line lists the rest
SAMPLE_COUNT = 3
NO_PINGS = discord.AllowedMentions.none()


@dataclass
class Hit:
    ts: float
    user_id: int
    app_id: int
    app_name: str
    channel_id: int
    message: Any = None


@dataclass
class Finding:
    kind: str                        # "user", "raid" or "late" (used a raid app after the mark)
    app_id: int
    app_name: str
    hits: list = field(default_factory=list)
    users: list = field(default_factory=list)

    @property
    def span(self) -> float:
        if not self.hits:
            return 0.0
        return max(h.ts for h in self.hits) - min(h.ts for h in self.hits)


# --- spotting the messages -------------------------------------------------------------
def invoker_of(message: Any) -> int | None:
    """Who ran the app that posted this, if it is an app installed to their own account."""
    author = getattr(message, "author", None)
    if not getattr(author, "bot", False):
        return None
    meta = getattr(message, "interaction_metadata", None)
    if meta is None:
        return None
    try:
        if not meta.is_user_integration() or meta.is_guild_integration():
            return None
    except Exception:
        return None
    return getattr(getattr(meta, "user", None), "id", None)


def _is_staff(member: Any) -> bool:
    from lib.core.behaviour_watch import is_staff
    return is_staff(member)


def hit_from_message(message: Any) -> Hit | None:
    if getattr(message, "guild", None) is None:
        return None
    uid = invoker_of(message)
    if uid is None or uid == USERS.OGGERS:
        return None
    author = message.author
    app_id = int(getattr(message, "application_id", None) or author.id)
    allowed = config.APP_SPAM_ALLOWED_APP_IDS
    if app_id in allowed or author.id in allowed:
        return None
    channel = message.channel
    channel_ids = {getattr(channel, "id", None), getattr(channel, "parent_id", None)}
    if channel_ids & set(config.APP_SPAM_ALLOWED_CHANNEL_IDS):
        return None
    member = message.guild.get_member(uid)
    if member is not None and _is_staff(member):
        return None
    created = getattr(message, "created_at", None)
    ts = created.timestamp() if created is not None else time.time()
    name = getattr(author, "name", None) or str(app_id)
    return Hit(ts, uid, app_id, name, getattr(channel, "id", 0), message)


# --- the windows -----------------------------------------------------------------------
class AppSpamTracker:
    """All the state, and none of the Discord calls, so a raid can be replayed in a test."""

    def __init__(self) -> None:
        self.by_user: dict[int, deque] = defaultdict(deque)
        self.by_app: dict[int, deque] = defaultdict(deque)
        self.blocked: dict[int, float] = {}       # user -> delete their app posts until
        self.raid_apps: dict[int, float] = {}     # app -> block anyone using it until

    def release(self, user_id: int) -> None:
        self.blocked.pop(int(user_id), None)

    def _prune(self, now: float) -> None:
        keep = max(config.APP_SPAM_USER_WINDOW_SECONDS, config.APP_SPAM_RAID_WINDOW_SECONDS)
        for table in (self.by_user, self.by_app):
            for key in list(table):
                q = table[key]
                while q and q[0].ts < now - keep:
                    q.popleft()
                if not q:
                    del table[key]
        for table in (self.blocked, self.raid_apps):
            for key in [k for k, until in table.items() if until <= now]:
                del table[key]

    def _block(self, users, now: float) -> None:
        for uid in users:
            self.blocked[uid] = now + config.APP_SPAM_CLEANUP_SECONDS

    def _forget(self, users) -> None:
        for uid in users:
            self.by_user.pop(uid, None)
        for key in list(self.by_app):
            self.by_app[key] = deque(h for h in self.by_app[key] if h.user_id not in users)

    def observe(self, hit: Hit) -> tuple[str, Finding | None]:
        """'cleanup' (already blocked: delete it), 'trigger' (act on the finding) or 'seen'."""
        now = hit.ts
        self._prune(now)
        if hit.user_id in self.blocked:
            return "cleanup", None
        if hit.app_id in self.raid_apps:
            self._block([hit.user_id], now)
            return "trigger", Finding("late", hit.app_id, hit.app_name, [hit], [hit.user_id])

        self.by_user[hit.user_id].append(hit)
        self.by_app[hit.app_id].append(hit)

        raid = [h for h in self.by_app[hit.app_id]
                if h.ts >= now - config.APP_SPAM_RAID_WINDOW_SECONDS]
        raiders = list(dict.fromkeys(h.user_id for h in raid))
        if (len(raid) >= config.APP_SPAM_RAID_COUNT
                and len(raiders) >= config.APP_SPAM_RAID_MIN_USERS):
            self._block(raiders, now)
            self.raid_apps[hit.app_id] = now + config.APP_SPAM_CLEANUP_SECONDS
            # Everything those people posted in the window goes, not only this app's.
            hits = [h for uid in raiders for h in self.by_user.get(uid, ())]
            self._forget(set(raiders))
            hits.sort(key=lambda h: h.ts)
            return "trigger", Finding("raid", hit.app_id, hit.app_name, hits, raiders)

        mine = [h for h in self.by_user[hit.user_id]
                if h.ts >= now - config.APP_SPAM_USER_WINDOW_SECONDS]
        if len(mine) >= config.APP_SPAM_USER_COUNT:
            self._block([hit.user_id], now)
            hits = list(self.by_user[hit.user_id])
            self._forget({hit.user_id})
            return "trigger", Finding("user", hit.app_id, hit.app_name, hits, [hit.user_id])
        return "seen", None


TRACKER = AppSpamTracker()


def release(user_id: int) -> None:
    """Stop deleting someone's app posts (staff lifted the timeout: it was a false positive)."""
    TRACKER.release(user_id)


# --- acting ----------------------------------------------------------------------------
async def _delete_one(message: Any) -> bool:
    try:
        await message.delete()
        return True
    except discord.NotFound:
        return True
    except Exception:
        log.debug("app spam: could not delete %s", getattr(message, "id", "?"), exc_info=True)
        return False


async def delete_hits(hits) -> int:
    """Delete the burst, a channel at a time: bulk where there are several, one call each."""
    by_channel: dict[int, list] = defaultdict(list)
    for h in hits:
        if h.message is not None:
            by_channel[h.channel_id].append(h.message)
    deleted = 0
    for messages in by_channel.values():
        channel = getattr(messages[0], "channel", None)
        if len(messages) > 1 and hasattr(channel, "delete_messages"):
            try:
                for i in range(0, len(messages), 100):
                    await channel.delete_messages(messages[i:i + 100])
                deleted += len(messages)
                continue
            except Exception:
                log.debug("app spam: bulk delete failed, deleting one at a time", exc_info=True)
        for m in messages:
            deleted += await _delete_one(m)
    return deleted


async def _member(guild: Any, user_id: int) -> Any:
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            member = None
    return member


async def timeout_user(guild: Any, user_id: int, app_name: str) -> str:
    member = await _member(guild, user_id)
    if member is None:
        return "not in the server"
    minutes = config.APP_SPAM_TIMEOUT_MINUTES
    try:
        await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes),
                             reason=f"Automated: flooded chat with the app {app_name}"[:500])
        return f"timed out {minutes}m"
    except discord.Forbidden:
        return "timeout failed: missing permission or role hierarchy"
    except discord.HTTPException as e:
        return f"timeout failed: {e.__class__.__name__}"


def _clean(text: str, limit: int) -> str:
    text = discord.utils.escape_mentions(str(text or "")).replace("`", "'").strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def sample_of(message: Any) -> str:
    text = getattr(message, "content", "") or ""
    if not text.strip():
        for e in getattr(message, "embeds", None) or []:
            text = getattr(e, "title", None) or getattr(e, "description", None) or ""
            if text:
                break
    if not text.strip() and getattr(message, "attachments", None):
        text = "(attachment)"
    return _clean(text, 200) or "(empty)"


def build_report(finding: Finding, user_id: int, deleted: int, status: str,
                 report_only: bool) -> tuple[discord.Embed, discord.ui.View]:
    from lib.core.mod_actions import (
        APP_SPAM, ModAnalyseButton, ModBanButton, ModIgnoreButton, ModTimeoutButton,
        ModUntimeoutButton, action_view,
    )
    theirs = [h for h in finding.hits if h.user_id == user_id] or finding.hits
    apps = ", ".join(dict.fromkeys(f"**{_clean(h.app_name, 40)}** (`{h.app_id}`)" for h in theirs))
    channels = " ".join(dict.fromkeys(f"<#{h.channel_id}>" for h in finding.hits))
    title = {"user": "App spam", "raid": "App raid", "late": "App raid"}[finding.kind]
    title += " reported" if report_only else " blocked"
    if finding.kind == "late":
        what = (f"used {apps}, an app on their own account, while it was marked from an app "
                f"raid, so they were blocked on their first message.")
    else:
        span = max(1, round(finding.span))
        what = (f"used {apps}, an app on their own account, to post **{len(theirs)}** "
                f"messages in {span}s.")
    embed = discord.Embed(title=title, description=f"<@{user_id}> (`{user_id}`) {what}",
                          color=discord.Color.orange() if report_only else discord.Color.red(),
                          timestamp=discord.utils.utcnow())
    embed.add_field(name="Channels", value=channels or "?", inline=True)
    if report_only:
        done = "Report-only mode: nothing deleted, no timeout"
    else:
        done = (f"Deleted {deleted} · {status} · further posts from their apps deleted for "
                f"{config.APP_SPAM_CLEANUP_SECONDS // 60} min")
    embed.add_field(name="Action", value=done, inline=True)
    if finding.kind == "raid":
        others = [u for u in finding.users if u != user_id]
        shown = " ".join(f"<@{u}>" for u in others[:15])
        more = f" and {len(others) - 15} more" if len(others) > 15 else ""
        embed.add_field(
            name="Raid",
            value=(f"{len(finding.users)} accounts, {len(finding.hits)} messages in "
                   f"{max(1, round(finding.span))}s. With: {shown}{more}")[:1000],
            inline=False)
    samples = "\n".join(f"> {sample_of(h.message)}" for h in theirs[:SAMPLE_COUNT] if h.message)
    if samples:
        embed.add_field(name="Sample" if report_only else "Sample (deleted)",
                        value=samples[:1000], inline=False)
    embed.set_footer(text="Use External Apps can also be switched off server-wide with /anti-raid")
    if report_only:
        only = (ModTimeoutButton, ModAnalyseButton, ModBanButton, ModIgnoreButton)
    else:
        only = (ModUntimeoutButton, ModAnalyseButton, ModTimeoutButton, ModBanButton)
    return embed, action_view(APP_SPAM, user_id, only=only)


async def _police(client: Any) -> Any:
    channel = client.get_channel(CHANNELS.POLICE_STATION)
    if channel is None:
        try:
            channel = await client.fetch_channel(CHANNELS.POLICE_STATION)
        except Exception:
            channel = None
    return channel


async def act(client: Any, finding: Finding, guild: Any) -> None:
    """Delete the burst, time everyone in it out and post a card each. Never raises."""
    try:
        report_only = config.APP_SPAM_REPORT_ONLY
        deleted, status = {}, {}
        if not report_only:
            for uid in finding.users:
                status[uid] = await timeout_user(guild, uid, finding.app_name)
            for uid in finding.users:
                deleted[uid] = await delete_hits([h for h in finding.hits if h.user_id == uid])
        police = await _police(client)
        if police is None:
            log.warning("app spam: police station not found")
            return
        for uid in finding.users[:MAX_CARDS]:
            embed, view = build_report(finding, uid, deleted.get(uid, 0), status.get(uid, ""),
                                       report_only)
            await police.send(embed=embed, view=view, allowed_mentions=NO_PINGS)
        rest = finding.users[MAX_CARDS:]
        if rest:
            await police.send(
                f"…and {len(rest)} more in the same app raid, handled the same way: "
                + " ".join(f"<@{u}>" for u in rest)[:1800],
                allowed_mentions=NO_PINGS)
        log.info("app spam %s: app %s, users %s, %s", finding.kind, finding.app_id,
                 finding.users, "report only" if report_only else status)
    except Exception:
        log.exception("app spam: acting on a finding failed")


def handle_app_message(client: Any, message: Any) -> bool:
    """on_message hook. True when the message is being deleted and needs nothing else."""
    if not config.APP_SPAM_ENABLED:
        return False
    hit = hit_from_message(message)
    if hit is None:
        return False
    verdict, finding = TRACKER.observe(hit)
    report_only = config.APP_SPAM_REPORT_ONLY
    if verdict == "cleanup":
        if report_only:
            return False
        asyncio.create_task(_delete_one(message))
        return True
    if verdict == "trigger":
        asyncio.create_task(act(client, finding, message.guild))
        return not report_only
    return False
