"""Holiday colours for the staff roles, Server Booster and VIP. Hadidas's draft 3 from #cabinet (5 Nov 2026, the
"staff holidays" and "booster and VIP only days" charts), built at Oggers's request:

- Staff holidays: Halloween (24 Oct to 7 Nov) and Christmas (18 Dec to 1 Jan), a week either side of the day
  (Oggers, 11 Nov 2026), and Easter (Good Friday to Easter Monday)
  recolour every role in ROLES, staff and booster/VIP alike.
- Booster and VIP days: New Year (31 Dec to 2 Jan), Valentine's (12 to 15 Feb), St David's (1 Mar), St George's
  (23 Apr), Bonfire Night (5 Nov) and St Andrew's (30 Nov) recolour Server Booster and VIP only.
- Outside those, every role has its usual colours (NORMAL, as they were on 11 Nov 2026).

A role can have a gradient (primary to secondary colour, Discord's enhanced role colours) or one solid colour. If two
holidays overlap (St George's in Easter week, say), the one listed first in HOLIDAYS wins for the roles it covers, so
the short booster/VIP days come first.

Behind config.HOLIDAY_ROLE_COLOURS_ENABLED. It runs at 00:00 UK every day and once soon after boot. Each run compares
every role's live colours with the ones it should have today and edits only the ones that differ, so an ordinary
day makes no API calls. A run that changes something says so in #bot-usage-log and posts a one-liner in #cabinet.
With the flag off it does nothing, unless the last thing it put up was a holiday (data/json/holiday_role_colours.json),
in which case it puts the usual colours back once. The level roles' seasons are lib/features/seasonal_rank_colours.py,
a separate switch."""

import asyncio
import datetime as dt
import json
import logging
import os
from typing import Dict, List, Optional, Tuple

import discord
import pytz

import config
from config import CHANNELS, GUILD_ID
from lib.core.file_operations import atomic_write_json

logger = logging.getLogger(__name__)

UK = pytz.timezone("Europe/London")
STATE = os.path.join("data", "json", "holiday_role_colours.json")
PAUSE = 1.0  # between role edits, to stay clear of the role-edit rate limit (a staff holiday is 14 edits)

# key -> (role id, display name)
ROLES = {
    "booster": (959650957325635707, "Server Booster"),
    "vip": (1417558416637034658, "VIP"),
    "cabinet": (1250190944502943755, "Cabinet"),
    "pm": (960537891128352768, "Prime Minister"),
    "moderation": (1142861027181088768, "Minister of Moderation"),
    "border_force": (959500686746345542, "Border Force (MOD)"),
    "community": (1142859471245295676, "Minister of Community"),
    "pcso": (1132949441389797397, "Community Events Team (PCSO)"),
    "engineering": (1133479529764110357, "Minister of Engineering"),
    "turing": (959897000554397718, "Alan Turing"),
    "diplomacy": (1133479650782347385, "Minister of Diplomacy"),
    "diplomat": (959582102565830716, "UK Diplomat"),
    "art": (1133479348704383048, "Minister of Art and Design"),
    "banksy": (959897952787267595, "Banksy"),
}
BOOSTER_VIP = ("booster", "vip")

Colours = Tuple[str, Optional[str]]  # (primary, secondary or None for a solid colour)

# the usual colours, read off the live roles on 11 Nov 2026 (Alan Turing moved off teal to periwinkle that day,
# and Minister of Engineering became Cabinet gold to periwinkle: Oggers's picks)
NORMAL: Dict[str, Colours] = {
    "booster": ("#FF5DD6", "#FFDBDC"), "vip": ("#FF74DD", "#5AFFF1"), "cabinet": ("#FFCD33", "#DCB84E"),
    "pm": ("#FFCD33", "#FFF5D9"), "moderation": ("#FFCD33", "#2A73E5"), "border_force": ("#2A73E5", None),
    "community": ("#FFCD33", "#9D52D9"), "pcso": ("#8D6BEA", None), "engineering": ("#FFCD33", "#8C9EFF"),
    "turing": ("#8C9EFF", None), "diplomacy": ("#FFCD33", "#08EBFA"), "diplomat": ("#04B5C1", None),
    "art": ("#FFCD33", "#7FE035"), "banksy": ("#496A2F", None),
}


def easter_sunday(year: int) -> dt.date:
    """Western (Gregorian) Easter Sunday, by the anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return dt.date(year, month, day)


def _md(day: dt.date) -> Tuple[int, int]:
    return day.month, day.day


def _between(start: Tuple[int, int], end: Tuple[int, int]):
    """A date test for a yearly window, inclusive; one that wraps the new year (31 Dec to 2 Jan) works too."""
    if start <= end:
        return lambda day: start <= _md(day) <= end
    return lambda day: _md(day) >= start or _md(day) <= end


def _easter(day: dt.date) -> bool:
    sunday = easter_sunday(day.year)
    return sunday - dt.timedelta(days=2) <= day <= sunday + dt.timedelta(days=1)


# Checked in order: the first active holiday that covers a role sets its colours. Booster/VIP days come first so a
# one-day holiday inside a staff one (St George's in Easter week) still shows on boosters and VIPs.
HOLIDAYS: List[dict] = [
    {"key": "new_year", "name": "New Year", "emoji": "🎆", "when": _between((12, 31), (1, 2)), "colours": {
        "booster": ("#FFF4BB", "#FF7EBA"), "vip": ("#FF9D73", "#FF6FDB")}},
    {"key": "valentines", "name": "Valentine's", "emoji": "💘", "when": _between((2, 12), (2, 15)), "colours": {
        "booster": ("#FF4D8D", "#FFB3C7"), "vip": ("#C778FF", "#FF3D84")}},
    {"key": "st_davids", "name": "St David's Day", "emoji": "🐉", "when": _between((3, 1), (3, 1)), "colours": {
        "booster": ("#FFFFFF", "#6CFF9A"), "vip": ("#6CFF9A", "#FF4757")}},
    {"key": "st_georges", "name": "St George's Day", "emoji": "🌹", "when": _between((4, 23), (4, 23)), "colours": {
        "booster": ("#FF4757", "#FFFFFF"), "vip": ("#FFFFFF", "#FF4757")}},
    {"key": "bonfire", "name": "Bonfire Night", "emoji": "🎇", "when": _between((11, 5), (11, 5)), "colours": {
        "booster": ("#FF4FD8", "#4FC3FF"), "vip": ("#B98CFF", "#FFFFFF")}},
    {"key": "st_andrews", "name": "St Andrew's Day", "emoji": "🦄", "when": _between((11, 30), (11, 30)), "colours": {
        "booster": ("#4C8DFF", "#FFFFFF"), "vip": ("#FFFFFF", "#4C8DFF")}},
    {"key": "halloween", "name": "Halloween", "emoji": "🎃", "when": _between((10, 24), (11, 7)), "colours": {
        "booster": ("#FF6B00", "#3DCE6D"), "vip": ("#E2F1FF", "#7CFF6B"), "cabinet": ("#9B30FF", "#6B17C2"),
        "pm": ("#9B30FF", "#CD88FF"), "moderation": ("#9B30FF", "#2F6BFF"), "border_force": ("#2F6BFF", None),
        "community": ("#9B30FF", "#FF2E55"), "pcso": ("#FF2E55", None), "engineering": ("#9B30FF", "#7CFF6B"),
        "turing": ("#7CFF6B", None), "diplomacy": ("#9B30FF", "#FF702E"), "diplomat": ("#FF702E", None),
        "art": ("#9B30FF", "#FFC800"), "banksy": ("#FFC800", None)}},
    {"key": "christmas", "name": "Christmas", "emoji": "🎄", "when": _between((12, 18), (1, 1)), "colours": {
        "booster": ("#FF6584", "#65FFB8"), "vip": ("#1FB855", "#FFD447"), "cabinet": ("#C47D3B", "#9E5D20"),
        "pm": ("#FFFFFF", "#FF2E4D"), "moderation": ("#C47D3B", "#2563EB"), "border_force": ("#2563EB", None),
        "community": ("#C47D3B", "#38E54D"), "pcso": ("#38E54D", "#21A835"), "engineering": ("#0F8A3C", "#FF2E4D"),
        "turing": ("#FF2E4D", None), "diplomacy": ("#C47D3B", "#FFD700"), "diplomat": ("#FFD700", None),
        "art": ("#C47D3B", "#4AD9FF"), "banksy": ("#4AD9FF", None)}},
    {"key": "easter", "name": "Easter", "emoji": "🐣", "when": _easter, "colours": {
        "booster": ("#FF94D8", "#75F0C0"), "vip": ("#5EC8FF", "#FFE666"), "cabinet": ("#FFA347", "#5CE65C"),
        "pm": ("#FFBCFC", "#FFFFFF"), "moderation": ("#FFA347", "#4D7CFF"), "border_force": ("#4D7CFF", None),
        "community": ("#FFA347", "#FF4466"), "pcso": ("#FF4466", None), "engineering": ("#FFA347", "#1FFFB0"),
        "turing": ("#1FFFB0", None), "diplomacy": ("#FFA347", "#B77CFF"), "diplomat": ("#B77CFF", None),
        "art": ("#FFA347", "#7FE035"), "banksy": ("#7FE035", None)}},
]
# 11 Nov 2026 (Hadidas): Moderation keeps a blue on every holiday, as its base colour is blue again, and the staff
# roles that had holiday blues (Community and PCSO at Halloween and Easter; Engineering, Turing and Art at Christmas)
# take the reds Moderation used to have.
HOLIDAY_BY_KEY = {h["key"]: h for h in HOLIDAYS}


def active_holidays(day: dt.date) -> List[str]:
    return [h["key"] for h in HOLIDAYS if h["when"](day)]


def plan_for(day: dt.date) -> Dict[str, Tuple[Colours, Optional[str]]]:
    """Every role's colours for `day` and the holiday giving them (None for the usual colours)."""
    plan = {}
    active = [HOLIDAY_BY_KEY[k] for k in active_holidays(day)]
    for key in ROLES:
        source = next((h for h in active if key in h["colours"]), None)
        plan[key] = (source["colours"][key], source["key"]) if source else (NORMAL[key], None)
    return plan


def _hex(code: Optional[str]) -> Optional[int]:
    return int(code.lstrip("#"), 16) if code else None


def _value(colour) -> Optional[int]:
    return None if colour is None else getattr(colour, "value", colour)


def _last() -> Optional[List[str]]:
    try:
        with open(STATE) as f:
            return json.load(f).get("applied")
    except (OSError, ValueError):
        return None


def _announce(started: List[str], ended: List[str]) -> Optional[str]:
    if started:
        return " · ".join(f"{HOLIDAY_BY_KEY[k]['emoji']} {HOLIDAY_BY_KEY[k]['name']} role colours are on" for k in started)
    if ended:
        return "🎨 Staff, booster and VIP colours are back to the usual ones"
    return None


async def apply_holiday_role_colours(client, today: Optional[dt.date] = None, enabled: Optional[bool] = None):
    """Bring the roles' colours in line with today. Returns (holidays, changed, failed), or None when there was
    nothing to do."""
    today = today or dt.datetime.now(UK).date()
    if enabled is None:
        enabled = getattr(config, "HOLIDAY_ROLE_COLOURS_ENABLED", False)
    last = _last()
    if enabled:
        plan = plan_for(today)
    elif last:  # switched off with a holiday up: put the usual colours back
        plan = {key: (NORMAL[key], None) for key in ROLES}
    else:
        return None
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        return None

    holidays = sorted({src for _, src in plan.values() if src}, key=list(HOLIDAY_BY_KEY).index)
    changed = failed = 0
    for key, ((primary, secondary), source) in plan.items():
        role_id, label = ROLES[key]
        role = guild.get_role(role_id)
        if role is None:
            logger.warning("holiday colours: %s role %s not found", label, role_id)
            failed += 1
            continue
        want_p, want_s = _hex(primary), _hex(secondary)
        if _value(role.colour) == want_p and _value(getattr(role, "secondary_colour", None)) == want_s:
            continue
        why = f"Holiday role colours: {HOLIDAY_BY_KEY[source]['name'] if source else 'usual colours'}"
        try:
            await role.edit(colour=discord.Colour(want_p),
                            secondary_colour=discord.Colour(want_s) if want_s is not None else None, reason=why)
            changed += 1
        except Exception:
            logger.error("holiday colours: couldn't set %s to %s/%s", label, primary, secondary, exc_info=True)
            failed += 1
        if PAUSE:
            await asyncio.sleep(PAUSE)

    # What's up is remembered even when a role failed, so turning the flag off still restores; "nothing up" is only
    # written once every role is back, so a partial restore is tried again next time.
    if holidays or not failed:
        atomic_write_json(STATE, {"applied": holidays, "on": today.isoformat()})

    if changed or failed:
        what = ", ".join(HOLIDAY_BY_KEY[k]["name"] for k in holidays) + " colours applied" if holidays \
            else "usual colours restored"
        line = f"🎨 Staff/booster/VIP roles: {what} ({changed} changed" + (f", {failed} failed" if failed else "") + ")"
        logger.info(line)
        try:
            channel = guild.get_channel(CHANNELS.BOT_USAGE_LOG)
            if channel is not None:
                await channel.send(line)
        except Exception:
            logger.warning("holiday colours: couldn't post to the usage log", exc_info=True)
    if changed:
        text = _announce([k for k in holidays if k not in (last or [])], [k for k in (last or []) if k not in holidays])
        if text:
            try:
                cabinet = guild.get_channel(CHANNELS.CABINET)
                if cabinet is not None:
                    await cabinet.send(text, allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                logger.warning("holiday colours: couldn't post in #cabinet", exc_info=True)
    return holidays, changed, failed
