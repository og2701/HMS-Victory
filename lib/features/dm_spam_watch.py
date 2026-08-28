"""Watch for members Discord has caught sending unusual volumes of DMs.

Discord stamps `unusual_dm_activity_until` on the guild member object when its own spam
detection trips on an account - the same thing that puts the "unusual DM activity" warning
in the client. It is a strong signal: measured across the guild, the accounts carrying it
are overwhelmingly the bulk-generated dating and crypto scam sort.

Two awkward facts shape this module.

discord.py drops the field. It is not in the library's Member at all, so the parsed objects
can never show it and there is no gateway event we can read it from. The only way to see it
is to ask the API for the raw member JSON ourselves, which is why this sweeps over HTTP
rather than reading from cache.

The flag runs 24 hours and is set AFTER the spamming starts, not on arrival. Across every
flagged member in the guild the earliest any flag expired was exactly 24 hours after that
member joined, so nobody turns up already carrying one. That rules it out as a join gate -
it is a "this member has just DM-spammed your people" alarm, and the value is in mods seeing
it while the account is still around to act on.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import discord

import config
from lib.core.file_operations import load_json_file, save_json_file
from lib.core.mod_actions import DM_SPAM, action_view

logger = logging.getLogger(__name__)

STATE_FILE = config.DM_SPAM_FLAG_FILE

# A first run that would announce a backlog is noise, not news. Above this many unseen
# flags, seed quietly and let the next sweep report genuine new ones.
FIRST_RUN_ALERT_LIMIT = 10
PAGE = 1000

def _parse(stamp):
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


async def fetch_flagged_members(client, now=None):
    """Every member whose unusual DM flag is live right now, straight from the API.

    Returns {user id: {name, until, joined, roles}}. Goes through client.http so the
    library's rate limiter owns the pacing - a full sweep of a 12k guild is 13 requests.
    """
    now = now or datetime.now(timezone.utc)
    found, after, pages = {}, 0, 0
    while True:
        # get_members hands back the untouched JSON - it is the parsing into Member that
        # loses the field, not the fetch.
        batch = await client.http.get_members(config.GUILD_ID, PAGE, after)
        if not batch:
            break
        pages += 1
        for m in batch:
            until = _parse(m.get("unusual_dm_activity_until"))
            if until is None or until <= now:
                continue
            user = m.get("user") or {}
            found[str(user.get("id"))] = {
                "name": user.get("username"),
                "until": m["unusual_dm_activity_until"],
                "joined": m.get("joined_at"),
                "roles": len(m.get("roles") or []),
            }
        after = max(int(m["user"]["id"]) for m in batch)
        if len(batch) < PAGE:
            break
    logger.debug(f"dm spam sweep: {pages} pages, {len(found)} live flags")
    return found


def _embed(uid, info, now=None):
    now = now or datetime.now(timezone.utc)
    until = _parse(info.get("until"))
    joined = _parse(info.get("joined"))
    created = discord.utils.snowflake_time(int(uid))

    embed = discord.Embed(
        title="📨 Discord flagged this member for unusual DM activity",
        description=f"<@{uid}> `{uid}`" + (f" · `{info['name']}`" if info.get("name") else ""),
        colour=0xE74C3C)
    if until is not None:
        hours = max(0, (until - now).total_seconds() / 3600)
        embed.add_field(name="Flag lifts", value=f"<t:{int(until.timestamp())}:R> ({hours:.0f}h)",
                        inline=True)
    if joined is not None:
        embed.add_field(name="Joined", value=f"<t:{int(joined.timestamp())}:R>", inline=True)
    embed.add_field(name="Account made", value=f"<t:{int(created.timestamp())}:R>", inline=True)
    embed.set_footer(text="Discord's own spam detection · the flag lasts 24h and is set "
                          "after the DMs go out")
    return embed


async def sweep(client, now=None):
    """One pass: find live flags, report the ones we have not reported, remember them.

    Expired flags are dropped from state rather than kept, so an account caught a second
    time weeks later is reported again instead of being silently remembered as old news.
    """
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    try:
        live = await fetch_flagged_members(client, now=now)
    except discord.HTTPException as e:
        logger.warning(f"dm spam sweep failed: {e}")
        return {}
    except Exception:
        logger.error("dm spam sweep blew up", exc_info=True)
        return {}

    state = load_json_file(STATE_FILE) or {}
    first_run = not state
    # An account flagged anew (or re-flagged in a later incident after expiration) counts
    # as new; minor sliding-window bumps during the same active 24h incident do not.
    new = {}
    for uid, info in live.items():
        prev = state.get(uid)
        if not prev:
            new[uid] = info
        else:
            prev_until = _parse(prev.get("until"))
            curr_until = _parse(info.get("until"))
            if prev_until is not None and curr_until is not None:
                # If expiry moved by more than 12 hours, treat as a separate incident
                if (curr_until - prev_until).total_seconds() > 12 * 3600:
                    new[uid] = info
            elif prev.get("until") != info.get("until"):
                new[uid] = info
    save_json_file(STATE_FILE, live)

    took = time.monotonic() - started
    logger.info(f"dm spam sweep: {len(live)} live, {len(new)} new, {took:.1f}s")
    if not new:
        return {}
    if first_run and len(new) > FIRST_RUN_ALERT_LIMIT:
        logger.warning(f"dm spam watch seeded with {len(new)} existing flags, not announcing")
        return {}

    channel = client.get_channel(config.CHANNELS.POLICE_STATION)
    if channel is None:
        logger.warning("dm spam watch has flags to report but no police station channel")
        return new
    for uid, info in sorted(new.items(), key=lambda kv: kv[1]["until"]):
        try:
            await channel.send(embed=_embed(uid, info, now=now),
                               view=action_view(DM_SPAM, uid))
        except Exception:
            logger.error(f"could not report the DM flag on {uid}", exc_info=True)
        await asyncio.sleep(1)
    return new
