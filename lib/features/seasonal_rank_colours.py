"""The 20 chat level roles (Serf to Royal Duke) change colour with the seasons, by UK month: winter Dec to Feb,
spring Mar to May, summer Jun to Aug, autumn Sep to Nov. The palettes are Hadidas's, agreed in #cabinet on
3-4 November 2026 (his message 1567151094646181991, approved by Dmze and Oggers), each listed Serf first.

Behind config.SEASONAL_RANK_COLOURS_ENABLED. It runs at 00:00 UK on the first day of each season and once soon
after boot, since a cron that comes round while the bot is down is skipped and would leave the wrong season up for
three months. Each run compares every role's live colour with the one it should have and edits only the ones that
differ, so a restart with everything right makes no API calls at all. A run that changes something says so in
#bot-usage-log and posts a one-liner in #cabinet. With the flag off it does nothing, unless
the last thing it put up was a season (data/json/seasonal_rank_colours.json), in which case it puts the original
colours back once."""

import asyncio
import datetime as dt
import json
import logging
import os

import discord
import pytz

import config
from config import CHANNELS, GUILD_ID
from lib.core.constants import CHAT_LEVEL_ROLE_THRESHOLDS
from lib.core.file_operations import atomic_write_json

logger = logging.getLogger(__name__)

UK = pytz.timezone("Europe/London")
STATE = os.path.join("data", "json", "seasonal_rank_colours.json")
# between role edits, to stay well clear of the role-edit rate limit (a switch is 20 edits)
PAUSE = 1.0

PALETTES = {
    "spring": ["#7C3D61", "#904B70", "#A45880", "#B66891", "#C57AA1", "#D18CB0", "#DC9FBD", "#E5B1C9", "#EABFD0", "#EBC4C8",
               "#EAC4C2", "#E8C5BC", "#E7C5B6", "#DCC9A5", "#C8D09A", "#B3D39A", "#9CD4A0", "#8ACDA2", "#85C3A8", "#A8E0C0"],
    "summer": ["#EA906D", "#EC9773", "#ED9E79", "#EEA47F", "#F0AB85", "#F1B18B", "#F2B891", "#F3BE97", "#EEC5A2", "#EACBAD",
               "#E4D1B7", "#DFD7C2", "#CCD6C9", "#B9D5CF", "#A4D3D6", "#8DD1DC", "#7CCBDD", "#6AC4DE", "#56BEDF", "#3DB7E0"],
    "autumn": ["#601A20", "#6F1E1D", "#7F241B", "#8E2A19", "#952D18", "#9D3118", "#AB3917", "#B94216", "#C64C16", "#CD5117",
               "#D25718", "#DC641B", "#E47220", "#EA8028", "#EE882D", "#EE9033", "#F19F40", "#F4B052", "#F6C16B", "#F9D58C"],
    "winter": ["#143258", "#183D68", "#1D4978", "#20507F", "#245687", "#2D6395", "#3872A3", "#3E79A9", "#4581B0", "#5390BC",
               "#649FC7", "#76AED1", "#80B5D5", "#89BCDA", "#9CCAE2", "#B0D7E9", "#C3E2EF", "#D5ECF4", "#E6F5F9", "#F5FCFF"],
    # the colours the roles had before any of this (4 Nov 2026), put back when the flag's turned off
    "original": ["#6D001A", "#820022", "#96002A", "#AA0032", "#BE0039", "#CF122A", "#DF231C", "#EF340E", "#F74907", "#FF5E00",
                 "#FF6B00", "#FF7700", "#FF7E00", "#FF8400", "#FF8400", "#FF8400", "#FF8400", "#FF8400", "#FF8400", "#FF8400"],
}

# what #cabinet is told when a switch actually changes colours
ANNOUNCE = {"spring": "🌸 Spring rank colours are on", "summer": "☀️ Summer rank colours are on",
            "autumn": "🍂 Autumn rank colours are on", "winter": "❄️ Winter rank colours are on",
            "original": "🎨 Rank colours are back to the usual ones"}

SEASON_BY_MONTH = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                   6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}


def season_for(day: dt.date) -> str:
    return SEASON_BY_MONTH[day.month]


def _last() -> str | None:
    try:
        with open(STATE) as f:
            return json.load(f).get("applied")
    except (OSError, ValueError):
        return None


def wanted(day: dt.date, enabled: bool) -> str | None:
    """The palette the roles should have on `day`, or None to leave them as they are."""
    if enabled:
        return season_for(day)
    last = _last()
    return "original" if last and last != "original" else None


async def apply_seasonal_rank_colours(client, today: dt.date | None = None, enabled: bool | None = None):
    """Bring the level roles' colours in line with what they should be today. Returns (palette, changed, failed),
    or None when there was nothing to do."""
    today = today or dt.datetime.now(UK).date()
    if enabled is None:
        enabled = getattr(config, "SEASONAL_RANK_COLOURS_ENABLED", False)
    name = wanted(today, enabled)
    if name is None:
        return None
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        return None

    changed = failed = 0
    for (_, role_id), code in zip(CHAT_LEVEL_ROLE_THRESHOLDS, PALETTES[name]):
        role = guild.get_role(role_id)
        if role is None:
            logger.warning("seasonal colours: level role %s not found", role_id)
            failed += 1
            continue
        want = int(code.lstrip("#"), 16)
        if role.colour.value == want:
            continue
        try:
            await role.edit(colour=discord.Colour(want), reason=f"Seasonal rank colours: {name}")
            changed += 1
        except Exception:
            logger.error("seasonal colours: couldn't set %s to %s", role.name, code, exc_info=True)
            failed += 1
        if PAUSE:
            await asyncio.sleep(PAUSE)

    # A season is remembered even when a role failed, so turning the flag off still restores; the original palette
    # is only marked done once every role is back, so a partial restore is tried again next time.
    if name != "original" or not failed:
        atomic_write_json(STATE, {"applied": name, "on": today.isoformat()})

    if changed or failed:
        what = "original colours restored" if name == "original" else f"{name} colours applied"
        line = f"🎨 Rank roles: {what} ({changed} changed" + (f", {failed} failed" if failed else "") + ")"
        logger.info(line)
        try:
            channel = guild.get_channel(CHANNELS.BOT_USAGE_LOG)
            if channel is not None:
                await channel.send(line)
        except Exception:
            logger.warning("seasonal colours: couldn't post to the usage log", exc_info=True)
    if changed:
        try:
            cabinet = guild.get_channel(CHANNELS.CABINET)
            if cabinet is not None:
                await cabinet.send(ANNOUNCE[name], allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.warning("seasonal colours: couldn't post in #cabinet", exc_info=True)
    return name, changed, failed
