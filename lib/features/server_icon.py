"""The server's icon through Halloween: a different Halloween icon each day until 31 October, taking turns in the
order below, then on 1 November back to the usual one (data/server_icons). Checked just after midnight UK time and
again soon after the bot starts, so a restart over midnight doesn't miss a day. The icon's only uploaded when it
isn't already the day's (data/json/server_icon.json remembers what was put up last), so once it's back to the usual
one it's left alone, and an icon someone sets by hand later stays."""

import datetime as dt
import json
import logging
import os

import pytz

from config import GUILD_ID
from lib.core.file_operations import atomic_write_json

logger = logging.getLogger(__name__)

UK = pytz.timezone("Europe/London")
ICONS = os.path.join("data", "server_icons")
STATE = os.path.join("data", "json", "server_icon.json")
HALLOWEEN = ["lantern", "witch", "franken", "hellfire", "scare", "mummy"]
# the run: the witch went up by hand on 9 October 2026, and the rest follow on from it a day at a time
START, FIRST = dt.date(2026, 10, 9), HALLOWEEN.index("witch")
END = dt.date(2026, 10, 31)
# a week after Halloween to get back to the usual icon, should the bot be down on the 1st
GRACE = 7


def icon_for(day: dt.date) -> str | None:
    """The icon `day` should have, as a file, or None to leave the server's as it is."""
    if START <= day <= END:
        return os.path.join(ICONS, "halloween", f"{HALLOWEEN[(FIRST + (day - START).days) % len(HALLOWEEN)]}.gif")
    if END < day <= END + dt.timedelta(days=GRACE):
        return os.path.join(ICONS, "default.png")
    return None


def _last() -> str | None:
    try:
        with open(STATE) as f:
            return json.load(f).get("icon")
    except (OSError, ValueError):
        return None


async def update_server_icon(client, today: dt.date | None = None) -> str | None:
    """Put up today's icon if it isn't up already. Returns the file put up, if one was."""
    today = today or dt.datetime.now(UK).date()
    want = icon_for(today)
    if want is None or want == _last():
        return None
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        return None
    try:
        with open(want, "rb") as f:
            await guild.edit(icon=f.read(), reason=f"Halloween icons: {os.path.basename(want)} for {today:%d %B}")
    except Exception:
        logger.error("couldn't set the server icon to %s", want, exc_info=True)
        return None
    atomic_write_json(STATE, {"icon": want, "on": today.isoformat()})
    logger.info("server icon set to %s for %s", want, today)
    return want
